"""
@file routePlanner.py
@brief Off-board route planning for the AVRA hackathon demo.

planRoute() takes a user-defined area and monitoring subareas (4 GPS corners each) and
returns an ordered waypoint list that circles each subarea on a buffer ring.

@par Function notes and assumptions (keep in sync with the requirements)
- Land: the UI is assumed to only let the user pick sea areas, so every point that reaches
  the planner is sea. The planner does NOT check land. An optional `isAllowed` hook lets a
  land mask or coast-distance check (MSN-13, at most about 3 km from the coast) be plugged
  in later. Only ring corners, start and end are checked with it, not the straight legs
  between them.
- Transit legs are shortest paths on a visibility graph whose obstacles are the buffered
  rings of ALL subareas (including skipped ones), so the vessel never crosses a farm
  (cages and moorings are assumed off limits). Open water is assumed everywhere else.
- Rings are mitered offsets of the subarea quadrilateral (corners are slightly longer than
  a rounded offset).
- A ring that extends outside the user-defined area is NOT skipped and NOT clipped. The
  route follows the full ring and a warning is added to the plan.
- The waypoint list holds corners only. Sampling every `sampleSpacingMeters` is NOT
  expanded into waypoints: legs flagged `isSamplingLeg` tell the onboard sampler where to
  trigger samples (FUN-3). sampleSchedule() expands them into explicit points when needed.
- All subareas have equal weight for now, but `priority` is already carried through.
- The budget is a distance cap (30 km default, margin already built into the requirement).
  Budget.fromEnergy() maps a power model to a cap, since speed is fixed near 2 knots.
- Geometry works in a local metric frame (equirectangular, fine at kilometre scale).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

earthRadiusMeters = 6371000.0
knotToMetersPerSecond = 0.514444
defaultCruiseSpeedMetersPerSecond = 2.0 * knotToMetersPerSecond
secondsPerHour = 3600.0


@dataclass(frozen=True)
class GeoPoint:
    """
    @brief A position on the globe in decimal degrees.
    """
    latitude: float
    longitude: float


@dataclass
class SubArea:
    """
    @brief A region to monitor, for example one aquaculture site.
    @param identifier Unique name of the subarea.
    @param corners Four GPS corners in any order (must form a convex shape).
    @param priority Relative importance, all equal for now.
    """
    identifier: str
    corners: list[GeoPoint]
    priority: float = 1.0


@dataclass
class SamplingSpec:
    """
    @brief Defines what collecting data at a subarea means.
    @param bufferMeters Distance of the sampling ring from the subarea edge (to be confirmed).
    @param sampleSpacingMeters Distance between samples along the ring.
    """
    bufferMeters: float = 50.0
    sampleSpacingMeters: float = 5.0


@dataclass
class Budget:
    """
    @brief Distance budget of one mission.
    @param maxDistanceMeters Maximum route length.
    @param marginFraction Extra derating, 0 because margin is already in the 30 km figure.
    """
    maxDistanceMeters: float = 30000.0
    marginFraction: float = 0.0

    @property
    def usableDistanceMeters(self) -> float:
        return self.maxDistanceMeters * (1.0 - self.marginFraction)

    @staticmethod
    def fromEnergy(usableWattHours: float, averagePowerWatts: float,
                   speedMetersPerSecond: float = defaultCruiseSpeedMetersPerSecond,
                   marginFraction: float = 0.0) -> "Budget":
        """
        @brief Builds a distance budget from battery energy and average power draw.
        @param usableWattHours Usable battery energy.
        @param averagePowerWatts Average electrical power at cruise speed.
        @param speedMetersPerSecond Cruise speed.
        @param marginFraction Extra derating applied on top.
        @return The equivalent distance budget.
        """
        enduranceSeconds = usableWattHours / averagePowerWatts * secondsPerHour
        return Budget(maxDistanceMeters=enduranceSeconds * speedMetersPerSecond,
                      marginFraction=marginFraction)


class RouteStatus(Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INFEASIBLE = "infeasible"


@dataclass
class Waypoint:
    """
    @brief One point of the planned route.
    @param kind One of start, transit, ringEntry, ringCorner, ringExit, end. Without an
           end point the last waypoint is the ringExit of the last ring.
    @param subAreaIdentifier Subarea this waypoint belongs to, if any.
    @param isSamplingLeg True if the leg arriving at this waypoint is sampled.
    """
    latitude: float
    longitude: float
    kind: str
    subAreaIdentifier: Optional[str] = None
    isSamplingLeg: bool = False


@dataclass
class RoutePlan:
    """
    @brief Result of planRoute().
    @param skippedReasons Maps a skipped subarea identifier to the reason it was skipped.
    @param warnings Non-fatal remarks, or the failure reason when the status is infeasible.
    """
    status: RouteStatus
    waypoints: list[Waypoint] = field(default_factory=list)
    totalDistanceMeters: float = 0.0
    transitDistanceMeters: float = 0.0
    samplingDistanceMeters: float = 0.0
    estimatedDurationSeconds: float = 0.0
    estimatedSampleCount: int = 0
    coveredSubAreaIdentifiers: list[str] = field(default_factory=list)
    skippedReasons: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


PlanarPoint = tuple[float, float]


class LocalFrame:
    """
    @brief Converts between GPS points and a local planar frame in meters.
    @param origin GPS point that becomes the planar origin.
    """

    def __init__(self, origin: GeoPoint):
        self.origin = origin
        self.metersPerDegreeLatitude = math.radians(1.0) * earthRadiusMeters
        self.metersPerDegreeLongitude = (self.metersPerDegreeLatitude
                                         * math.cos(math.radians(origin.latitude)))

    def toPlanar(self, point: GeoPoint) -> PlanarPoint:
        return ((point.longitude - self.origin.longitude) * self.metersPerDegreeLongitude,
                (point.latitude - self.origin.latitude) * self.metersPerDegreeLatitude)

    def toGeographic(self, point: PlanarPoint) -> GeoPoint:
        return GeoPoint(self.origin.latitude + point[1] / self.metersPerDegreeLatitude,
                        self.origin.longitude + point[0] / self.metersPerDegreeLongitude)


def distanceBetween(first: PlanarPoint, second: PlanarPoint) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def crossProduct(origin: PlanarPoint, first: PlanarPoint, second: PlanarPoint) -> float:
    """
    @brief Z component of (first - origin) x (second - origin).
    @return Positive if second lies to the left of the line origin to first.
    """
    return ((first[0] - origin[0]) * (second[1] - origin[1])
            - (first[1] - origin[1]) * (second[0] - origin[0]))


def sortCounterClockwiseConvex(points: list[PlanarPoint]) -> list[PlanarPoint]:
    """
    @brief Sorts corners counter-clockwise so the corner order from the UI does not matter.
    @param points Polygon corners in any order.
    @return The corners in counter-clockwise order.
    @throws ValueError If fewer than three corners are given or the polygon is not convex.
    """
    if len(points) < 3:
        raise ValueError("need at least 3 corners")
    centerEast = sum(point[0] for point in points) / len(points)
    centerNorth = sum(point[1] for point in points) / len(points)
    ordered = sorted(points, key=lambda point: math.atan2(point[1] - centerNorth,
                                                          point[0] - centerEast))
    count = len(ordered)
    for index in range(count):
        turn = crossProduct(ordered[index], ordered[(index + 1) % count],
                            ordered[(index + 2) % count])
        if turn <= 1e-9:
            raise ValueError("corners are not a convex polygon")
    return ordered


def isInsideConvex(point: PlanarPoint, polygon: list[PlanarPoint],
                   toleranceMeters: float = 0.0) -> bool:
    """
    @brief Tests whether a point lies inside a counter-clockwise convex polygon.
    @param toleranceMeters Positive values accept points slightly outside, negative values
           require the point to be at least that deep inside.
    """
    count = len(polygon)
    for index in range(count):
        edgeStart, edgeEnd = polygon[index], polygon[(index + 1) % count]
        signedDistance = (crossProduct(edgeStart, edgeEnd, point)
                          / max(distanceBetween(edgeStart, edgeEnd), 1e-9))
        if signedDistance < -toleranceMeters:
            return False
    return True


def distanceToSegment(point: PlanarPoint, segmentStart: PlanarPoint,
                      segmentEnd: PlanarPoint) -> float:
    deltaEast = segmentEnd[0] - segmentStart[0]
    deltaNorth = segmentEnd[1] - segmentStart[1]
    fraction = (((point[0] - segmentStart[0]) * deltaEast
                 + (point[1] - segmentStart[1]) * deltaNorth)
                / max(deltaEast * deltaEast + deltaNorth * deltaNorth, 1e-12))
    fraction = max(0.0, min(1.0, fraction))
    closest = (segmentStart[0] + fraction * deltaEast, segmentStart[1] + fraction * deltaNorth)
    return distanceBetween(point, closest)


def distanceToPolygon(point: PlanarPoint, polygon: list[PlanarPoint]) -> float:
    if isInsideConvex(point, polygon):
        return 0.0
    count = len(polygon)
    return min(distanceToSegment(point, polygon[index], polygon[(index + 1) % count])
               for index in range(count))


def offsetConvexPolygon(polygon: list[PlanarPoint], offsetMeters: float) -> list[PlanarPoint]:
    """
    @brief Mitered outward offset of a counter-clockwise convex polygon.
    """
    count = len(polygon)
    offsetCorners = []
    for index in range(count):
        previous, corner, following = polygon[index - 1], polygon[index], polygon[(index + 1) % count]
        incomingEdge = (corner[0] - previous[0], corner[1] - previous[1])
        outgoingEdge = (following[0] - corner[0], following[1] - corner[1])
        incomingLength = math.hypot(*incomingEdge)
        outgoingLength = math.hypot(*outgoingEdge)
        incomingNormal = (incomingEdge[1] / incomingLength, -incomingEdge[0] / incomingLength)
        outgoingNormal = (outgoingEdge[1] / outgoingLength, -outgoingEdge[0] / outgoingLength)
        miterScale = offsetMeters / (1.0 + incomingNormal[0] * outgoingNormal[0]
                                     + incomingNormal[1] * outgoingNormal[1])
        offsetCorners.append((corner[0] + miterScale * (incomingNormal[0] + outgoingNormal[0]),
                              corner[1] + miterScale * (incomingNormal[1] + outgoingNormal[1])))
    return offsetCorners


def lengthInsideConvex(segmentStart: PlanarPoint, segmentEnd: PlanarPoint,
                       polygon: list[PlanarPoint], minimumDepthMeters: float = 1.0) -> float:
    """
    @brief Length of a segment lying deeper than minimumDepthMeters inside a convex polygon.
    @details Uses Cyrus-Beck clipping. Legs that hug the polygon boundary therefore count
             as free.
    @param polygon Counter-clockwise convex polygon.
    @return Length in meters of the part of the segment that is inside.
    """
    entryFraction, exitFraction = 0.0, 1.0
    count = len(polygon)
    for index in range(count):
        edgeStart, edgeEnd = polygon[index], polygon[(index + 1) % count]
        edgeLength = max(distanceBetween(edgeStart, edgeEnd), 1e-9)
        startDepth = crossProduct(edgeStart, edgeEnd, segmentStart) / edgeLength - minimumDepthMeters
        endDepth = crossProduct(edgeStart, edgeEnd, segmentEnd) / edgeLength - minimumDepthMeters
        if startDepth < 0 and endDepth < 0:
            return 0.0
        if startDepth < 0:
            entryFraction = max(entryFraction, startDepth / (startDepth - endDepth))
        elif endDepth < 0:
            exitFraction = min(exitFraction, startDepth / (startDepth - endDepth))
        if entryFraction >= exitFraction:
            return 0.0
    return (exitFraction - entryFraction) * distanceBetween(segmentStart, segmentEnd)


minimumBlockedLengthMeters = 0.5


def isLegFree(legStart: PlanarPoint, legEnd: PlanarPoint,
              obstacles: list[list[PlanarPoint]]) -> bool:
    """
    @brief Tests that a straight leg does not pass through the interior of any obstacle.
    """
    return all(lengthInsideConvex(legStart, legEnd, obstacle) < minimumBlockedLengthMeters
               for obstacle in obstacles)


class LegGraph:
    """
    @brief Shortest paths between start, end and every obstacle-ring corner.
    @details Legs may not pass through the interior of an obstacle ring. Paths are computed
             with the Floyd-Warshall algorithm, which is fast enough for the few dozen
             nodes expected here.
    @param points Planar position of every node.
    @param obstacles Counter-clockwise convex polygons that legs must not cross.
    """
    def __init__(self, points: list[PlanarPoint], obstacles: list[list[PlanarPoint]]):
        nodeCount = len(points)
        self.points = points
        unreachable = float("inf")
        self.distances = [[unreachable] * nodeCount for rowIndex in range(nodeCount)]
        self.nextNode: list[list[Optional[int]]] = [[None] * nodeCount for rowIndex in range(nodeCount)]
        for node in range(nodeCount):
            self.distances[node][node] = 0.0
            self.nextNode[node][node] = node
            for otherNode in range(node + 1, nodeCount):
                if isLegFree(points[node], points[otherNode], obstacles):
                    legLength = distanceBetween(points[node], points[otherNode])
                    self.distances[node][otherNode] = legLength
                    self.distances[otherNode][node] = legLength
                    self.nextNode[node][otherNode] = otherNode
                    self.nextNode[otherNode][node] = node
        for intermediate in range(nodeCount):
            for source in range(nodeCount):
                for target in range(nodeCount):
                    detour = self.distances[source][intermediate] + self.distances[intermediate][target]
                    if detour < self.distances[source][target] - 1e-9:
                        self.distances[source][target] = detour
                        self.nextNode[source][target] = self.nextNode[source][intermediate]

    def pathBetween(self, source: int, target: int) -> list[int]:
        """
        @brief Node sequence of the shortest path, both ends included.
        @details Falls back to a direct leg if the target is unreachable.
        """
        if self.nextNode[source][target] is None:
            return [source, target]
        path = [source]
        current = source
        while current != target:
            current = self.nextNode[current][target]
            path.append(current)
        return path


@dataclass
class Ring:
    """
    @brief Sampling ring around one subarea.
    @param graphNodes Node index in the LegGraph of every ring corner.
    """
    identifier: str
    corners: list[PlanarPoint]
    perimeterMeters: float
    center: PlanarPoint
    priority: float
    graphNodes: list[int] = field(default_factory=list)


def evaluateOrder(order: list[str], rings: dict[str, Ring], graph: LegGraph,
                  startNode: int, endNode: Optional[int]) -> tuple[float, list[int]]:
    """
    @brief Total distance (transit plus rings) of a visiting order.
    @details Chooses the best entry corner per ring (a ring is entered and left at the same
             corner) with a Viterbi search. Transit costs come from the obstacle-aware leg
             graph. If endNode is None the route is open-ended: it finishes at the last ring
             and no transit to an end point is counted.
    @return The total distance and the chosen entry corner index for every ring in order.
    """
    distances = graph.distances
    if not order:
        return (0.0 if endNode is None else distances[startNode][endNode]), []
    costToReachCorner = [distances[startNode][node] for node in rings[order[0]].graphNodes]
    cornerChoices: list[list[int]] = []
    for position in range(1, len(order)):
        previousRing, currentRing = rings[order[position - 1]], rings[order[position]]
        newCosts, bestPreviousCorners = [], []
        for node in currentRing.graphNodes:
            bestCost, bestPreviousCorner = min(
                (costToReachCorner[corner] + distances[previousRing.graphNodes[corner]][node], corner)
                for corner in range(len(previousRing.graphNodes)))
            newCosts.append(bestCost)
            bestPreviousCorners.append(bestPreviousCorner)
        cornerChoices.append(bestPreviousCorners)
        costToReachCorner = newCosts
    lastRing = rings[order[-1]]
    totalTransit, lastCorner = min(
        (costToReachCorner[corner]
         + (0.0 if endNode is None else distances[lastRing.graphNodes[corner]][endNode]), corner)
        for corner in range(len(lastRing.graphNodes)))
    entryCorners = [0] * len(order)
    entryCorners[-1] = lastCorner
    for position in range(len(order) - 1, 0, -1):
        entryCorners[position - 1] = cornerChoices[position - 1][entryCorners[position]]
    return totalTransit + sum(rings[identifier].perimeterMeters for identifier in order), entryCorners


def optimizeOrder(identifiers: list[str], rings: dict[str, Ring], graph: LegGraph,
                  startNode: int, endNode: Optional[int]) -> list[str]:
    """
    @brief Orders the rings with a nearest-neighbour start followed by 2-opt improvement.
    @details Good enough for the 5 to 15 sites expected.
    """
    remaining, order, currentNode = list(identifiers), [], startNode
    while remaining:
        nearest = min(remaining, key=lambda identifier: min(
            graph.distances[currentNode][node] for node in rings[identifier].graphNodes))
        remaining.remove(nearest)
        order.append(nearest)
        currentNode = min(rings[nearest].graphNodes,
                          key=lambda node: graph.distances[currentNode][node])
    bestDistance = evaluateOrder(order, rings, graph, startNode, endNode)[0]
    improved = True
    while improved and len(order) > 2:
        improved = False
        for first in range(len(order) - 1):
            for last in range(first + 1, len(order)):
                candidate = order[:first] + order[first:last + 1][::-1] + order[last + 1:]
                candidateDistance = evaluateOrder(candidate, rings, graph, startNode, endNode)[0]
                if candidateDistance < bestDistance - 1e-6:
                    order, bestDistance, improved = candidate, candidateDistance, True
    return order


def buildRings(subAreas: list[SubArea], areaPolygon: list[PlanarPoint], frame: LocalFrame,
               sampling: SamplingSpec, isAllowed: Optional[Callable[[GeoPoint], bool]],
               plan: RoutePlan) -> tuple[dict[str, Ring], dict[str, list[PlanarPoint]]]:
    """
    @brief Builds one buffered ring per usable subarea.
    @details Fills plan.skippedReasons and plan.warnings as a side effect. Rings leaving the
             area only produce a warning. Subareas that are invalid, outside the area or
             violate the allowed-region constraint are skipped.
    @return The usable rings and the obstacle ring of every subarea with valid geometry
            (skipped subareas still block transit).
    """
    rings: dict[str, Ring] = {}
    obstacles: dict[str, list[PlanarPoint]] = {}
    for subArea in subAreas:
        try:
            polygon = sortCounterClockwiseConvex([frame.toPlanar(corner) for corner in subArea.corners])
        except ValueError as error:
            plan.skippedReasons[subArea.identifier] = f"invalidGeometry: {error}"
            continue
        ringCorners = offsetConvexPolygon(polygon, sampling.bufferMeters)
        obstacles[subArea.identifier] = ringCorners
        if not all(isInsideConvex(corner, areaPolygon, toleranceMeters=1.0) for corner in polygon):
            plan.skippedReasons[subArea.identifier] = "outsideArea"
            plan.warnings.append(f"subarea {subArea.identifier} is outside the area and was not planned")
            continue
        if not all(isInsideConvex(corner, areaPolygon, toleranceMeters=1.0) for corner in ringCorners):
            plan.warnings.append(f"ring of {subArea.identifier} extends outside the area")
        if isAllowed and not all(isAllowed(frame.toGeographic(corner)) for corner in ringCorners):
            plan.skippedReasons[subArea.identifier] = "violatesConstraint"
            continue
        perimeter = sum(distanceBetween(ringCorners[index], ringCorners[(index + 1) % len(ringCorners)])
                        for index in range(len(ringCorners)))
        center = (sum(corner[0] for corner in polygon) / len(polygon),
                  sum(corner[1] for corner in polygon) / len(polygon))
        rings[subArea.identifier] = Ring(subArea.identifier, ringCorners, perimeter, center,
                                         subArea.priority)
    return rings, obstacles


def dropUntilWithinBudget(order: list[str], rings: dict[str, Ring], graph: LegGraph,
                          startNode: int, endNode: Optional[int], usableDistanceMeters: float,
                          plan: RoutePlan) -> list[str]:
    """
    @brief Removes subareas until the route fits the budget.
    @details Each step drops the subarea with the best ratio of distance saved to priority
             and records it in plan.skippedReasons.
    @return The remaining visiting order.
    """
    totalDistance = evaluateOrder(order, rings, graph, startNode, endNode)[0]
    while order and totalDistance > usableDistanceMeters:
        dropIdentifier, bestScore = None, -1.0
        for identifier in order:
            reducedDistance = evaluateOrder([other for other in order if other != identifier],
                                            rings, graph, startNode, endNode)[0]
            score = (totalDistance - reducedDistance) / max(rings[identifier].priority, 1e-9)
            if score > bestScore:
                dropIdentifier, bestScore = identifier, score
        plan.skippedReasons[dropIdentifier] = "exceedsBudget"
        order = optimizeOrder([other for other in order if other != dropIdentifier],
                              rings, graph, startNode, endNode)
        totalDistance = evaluateOrder(order, rings, graph, startNode, endNode)[0]
    return order


def planRoute(area: list[GeoPoint],
              subAreas: list[SubArea],
              startPoint: GeoPoint,
              endPoint: Optional[GeoPoint] = None,
              budget: Optional[Budget] = None,
              sampling: Optional[SamplingSpec] = None,
              cruiseSpeedMetersPerSecond: float = defaultCruiseSpeedMetersPerSecond,
              maxTransitOffsetMeters: float = 2000.0,
              isAllowed: Optional[Callable[[GeoPoint], bool]] = None) -> RoutePlan:
    """
    @brief Plans a route that circles every subarea on a buffer ring within the budget.
    @param area Four GPS corners of the user-defined area of interest.
    @param subAreas Subareas to monitor, four GPS corners each.
    @param startPoint Launch point (may be slightly outside the area).
    @param endPoint Optional end point. If omitted, the mission ends where the sampling
           ends: at the last sampled point of the last ring (no transit afterwards).
    @param budget Distance budget, defaults to 30 km.
    @param sampling Ring buffer and sample spacing.
    @param cruiseSpeedMetersPerSecond Speed used for the duration estimate.
    @param maxTransitOffsetMeters How far start and end may be from the area.
    @param isAllowed Optional hook for a land mask or coast-distance constraint.
    @return The route plan. Check status, skippedReasons and warnings before use.
    """
    budget = budget or Budget()
    sampling = sampling or SamplingSpec()
    plan = RoutePlan(status=RouteStatus.INFEASIBLE)

    areaCenter = GeoPoint(sum(corner.latitude for corner in area) / len(area),
                          sum(corner.longitude for corner in area) / len(area))
    frame = LocalFrame(areaCenter)
    try:
        areaPolygon = sortCounterClockwiseConvex([frame.toPlanar(corner) for corner in area])
    except ValueError as error:
        plan.warnings.append(f"area invalid: {error}")
        return plan

    startPlanar = frame.toPlanar(startPoint)
    endPlanar = frame.toPlanar(endPoint) if endPoint else None
    endpoints = [("start", startPoint, startPlanar)]
    if endPoint:
        endpoints.append(("end", endPoint, endPlanar))
    for endpointName, endpoint, endpointPlanar in endpoints:
        offsetMeters = distanceToPolygon(endpointPlanar, areaPolygon)
        if offsetMeters > maxTransitOffsetMeters:
            plan.warnings.append(f"{endpointName} is {offsetMeters:.0f} m from the area "
                                 f"(limit {maxTransitOffsetMeters:.0f} m)")
            return plan
        if offsetMeters > 0:
            plan.warnings.append(f"{endpointName} is outside the area ({offsetMeters:.0f} m)")
        if isAllowed and not isAllowed(endpoint):
            plan.warnings.append(f"{endpointName} violates the allowed-region constraint")
            return plan

    identifiers = [subArea.identifier for subArea in subAreas]
    if len(set(identifiers)) != len(identifiers):
        plan.warnings.append("duplicate subarea identifiers")
        return plan
    rings, obstacles = buildRings(subAreas, areaPolygon, frame, sampling, isAllowed, plan)

    graphPoints: list[PlanarPoint] = [startPlanar] + ([endPlanar] if endPoint else [])
    startNode, endNode = 0, (1 if endPoint else None)
    for identifier, obstacleCorners in obstacles.items():
        firstNode = len(graphPoints)
        graphPoints.extend(obstacleCorners)
        if identifier in rings:
            rings[identifier].graphNodes = list(range(firstNode, len(graphPoints)))
    for endpointName, endpoint, endpointPlanar in endpoints:
        for identifier, obstacleCorners in obstacles.items():
            if isInsideConvex(endpointPlanar, obstacleCorners, toleranceMeters=-1.0):
                plan.warnings.append(f"{endpointName} lies inside the ring of subarea {identifier}")
                return plan
    graph = LegGraph(graphPoints, list(obstacles.values()))

    order = optimizeOrder(list(rings), rings, graph, startNode, endNode)
    order = dropUntilWithinBudget(order, rings, graph, startNode, endNode,
                                  budget.usableDistanceMeters, plan)
    if not order:
        plan.warnings.append("no subarea could be covered")
        return plan

    entryCorners = evaluateOrder(order, rings, graph, startNode, endNode)[1]
    routePoints: list[tuple[PlanarPoint, str, Optional[str], bool]] = [
        (startPlanar, "start", None, False)]

    def appendTransit(fromNode: int, toNode: int) -> None:
        """
        @brief Appends the intermediate detour nodes between two graph nodes.
        """
        for node in graph.pathBetween(fromNode, toNode)[1:-1]:
            routePoints.append((graphPoints[node], "transit", None, False))

    currentNode = startNode
    for identifier, entryCorner in zip(order, entryCorners):
        ring = rings[identifier]
        cornerCount = len(ring.corners)
        appendTransit(currentNode, ring.graphNodes[entryCorner])
        routePoints.append((ring.corners[entryCorner], "ringEntry", identifier, False))
        for step in range(1, cornerCount):
            routePoints.append((ring.corners[(entryCorner + step) % cornerCount],
                                "ringCorner", identifier, True))
        routePoints.append((ring.corners[entryCorner], "ringExit", identifier, True))
        currentNode = ring.graphNodes[entryCorner]
    if endPoint:
        appendTransit(currentNode, endNode)
        routePoints.append((endPlanar, "end", None, False))

    for planar, kind, identifier, isSamplingLeg in routePoints:
        geographic = frame.toGeographic(planar)
        plan.waypoints.append(Waypoint(geographic.latitude, geographic.longitude, kind,
                                       identifier, isSamplingLeg))
    for index in range(1, len(routePoints)):
        legLength = distanceBetween(routePoints[index - 1][0], routePoints[index][0])
        if routePoints[index][3]:
            plan.samplingDistanceMeters += legLength
        else:
            plan.transitDistanceMeters += legLength
    plan.totalDistanceMeters = plan.transitDistanceMeters + plan.samplingDistanceMeters
    plan.estimatedDurationSeconds = plan.totalDistanceMeters / cruiseSpeedMetersPerSecond
    plan.estimatedSampleCount = int(plan.samplingDistanceMeters / sampling.sampleSpacingMeters)
    plan.coveredSubAreaIdentifiers = list(order)
    plan.status = RouteStatus.PARTIAL if plan.skippedReasons else RouteStatus.COMPLETE
    return plan


def sampleSchedule(plan: RoutePlan, spacingMeters: float = 5.0) -> list[GeoPoint]:
    """
    @brief Expands the sampled legs into explicit sample points.
    @details This is the "sequence of GPS points" view. The onboard side only needs the
             waypoints and the sampling flags.
    @param spacingMeters Distance between consecutive sample points.
    @return Sample points in route order.
    """
    if not plan.waypoints:
        return []
    frame = LocalFrame(GeoPoint(plan.waypoints[0].latitude, plan.waypoints[0].longitude))
    samples: list[GeoPoint] = []
    for previous, current in zip(plan.waypoints, plan.waypoints[1:]):
        if not current.isSamplingLeg:
            continue
        legStart = frame.toPlanar(GeoPoint(previous.latitude, previous.longitude))
        legEnd = frame.toPlanar(GeoPoint(current.latitude, current.longitude))
        sampleCount = max(1, round(distanceBetween(legStart, legEnd) / spacingMeters))
        for step in range(1, sampleCount + 1):
            fraction = step / sampleCount
            samples.append(frame.toGeographic((legStart[0] + fraction * (legEnd[0] - legStart[0]),
                                               legStart[1] + fraction * (legEnd[1] - legStart[1]))))
    return samples
