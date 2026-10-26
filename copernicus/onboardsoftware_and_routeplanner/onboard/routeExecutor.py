"""
@file routeExecutor.py
@brief Follows a received waypoint list with simple line-of-sight guidance (FUN-2).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from onboard.onboardConfiguration import OnboardConfiguration
from onboard.onboardGeometry import bearingDegrees, distanceBetweenPositions, toPlanarMeters
from onboard.onboardTypes import GeographicPosition, RouteWaypoint


@dataclass
class GuidanceCommand:
    """
    @brief Guidance output of one step.
    @param crossTrackErrorMeters Signed distance from the active leg, positive to the left.
    @param travelledLegIsSampling True if the leg travelled during this step is a sampling leg.
    """
    desiredHeadingDegrees: float
    desiredSpeedMetersPerSecond: float
    crossTrackErrorMeters: float
    travelledLegIsSampling: bool


class RouteExecutor:
    """
    @brief Holds the route and the progress along it.
    @details Progress is kept when the mode leaves nominal, so a paused route resumes where it stopped.
            The first leg starts at the position where the executor is first stepped.
    @param configuration Onboard configuration.
    """

    def __init__(self, configuration: OnboardConfiguration):
        self.configuration = configuration
        self.waypoints: list[RouteWaypoint] = []
        self.activeIndex = 0
        self.legStart: Optional[GeographicPosition] = None

    def loadRoute(self, waypoints: list[RouteWaypoint]) -> None:
        """
        @brief Replaces the route and restarts the progress.
        """
        self.waypoints = list(waypoints)
        self.activeIndex = 0
        self.legStart = None

    @property
    def hasPendingRoute(self) -> bool:
        return bool(self.waypoints) and self.activeIndex < len(self.waypoints)

    @property
    def isRouteComplete(self) -> bool:
        return bool(self.waypoints) and self.activeIndex >= len(self.waypoints)

    def remainingDistanceMeters(self, position: GeographicPosition) -> float:
        """
        @brief Distance from a position through all remaining waypoints.
        """
        if not self.hasPendingRoute:
            return 0.0
        total = distanceBetweenPositions(position, self.waypoints[self.activeIndex].position)
        for index in range(self.activeIndex, len(self.waypoints) - 1):
            total += distanceBetweenPositions(self.waypoints[index].position,
                                              self.waypoints[index + 1].position)
        return total

    def legCoordinates(self, position: GeographicPosition) -> tuple[float, float, float]:
        """
        @brief Position relative to the active leg.
        @return Along-track distance, signed cross-track distance and leg length in meters.
        """
        target = self.waypoints[self.activeIndex].position
        legEast, legNorth = toPlanarMeters(self.legStart, target)
        legLength = math.hypot(legEast, legNorth)
        if legLength < 1e-6:
            return 0.0, 0.0, 0.0
        eastUnit, northUnit = legEast / legLength, legNorth / legLength
        positionEast, positionNorth = toPlanarMeters(self.legStart, position)
        alongTrack = positionEast * eastUnit + positionNorth * northUnit
        crossTrack = eastUnit * positionNorth - northUnit * positionEast
        return alongTrack, crossTrack, legLength

    def advanceReachedWaypoints(self, position: GeographicPosition) -> None:
        """
        @brief Moves to the next leg while the active waypoint is reached.
        @details A waypoint is reached when within the acceptance radius or when the position is
                 at or beyond the end of the leg along the leg direction.
        """
        while self.activeIndex < len(self.waypoints):
            target = self.waypoints[self.activeIndex].position
            alongTrack, crossTrack, legLength = self.legCoordinates(position)
            reached = (legLength < 1e-6 or alongTrack >= legLength
                       or distanceBetweenPositions(position, target)
                       <= self.configuration.waypointAcceptanceRadiusMeters)
            if not reached:
                return
            self.legStart = target
            self.activeIndex += 1

    def step(self, position: GeographicPosition) -> Optional[GuidanceCommand]:
        """
        @brief Advances the progress and computes the guidance for the active leg.
        @return The guidance command, or None if there is no route to follow.
        """
        if not self.hasPendingRoute:
            return None
        if self.legStart is None:
            self.legStart = position
        travelledLegIsSampling = self.waypoints[self.activeIndex].isSamplingLeg
        self.advanceReachedWaypoints(position)
        if self.isRouteComplete:
            return GuidanceCommand(0.0, 0.0, 0.0, travelledLegIsSampling)
        alongTrack, crossTrack, legLength = self.legCoordinates(position)
        target = self.waypoints[self.activeIndex].position
        legEast, legNorth = toPlanarMeters(self.legStart, target)
        positionEast, positionNorth = toPlanarMeters(self.legStart, position)
        lookaheadAlong = min(max(alongTrack, 0.0) + self.configuration.lookaheadDistanceMeters,
                             legLength)
        aimEast = legEast / legLength * lookaheadAlong - positionEast
        aimNorth = legNorth / legLength * lookaheadAlong - positionNorth
        if math.hypot(aimEast, aimNorth) < 1e-6:
            aimEast, aimNorth = legEast - positionEast, legNorth - positionNorth
        return GuidanceCommand(bearingDegrees(aimEast, aimNorth),
                               self.configuration.cruiseSpeedMetersPerSecond,
                               crossTrack, travelledLegIsSampling)
