"""
@file demoRoutePlanner.py
@brief Demo of planRoute(): a 4 x 4 km area (16 km^2) with six aquaculture-sized subareas.
"""
from routePlanner import (Budget, GeoPoint, LocalFrame, RoutePlan, SamplingSpec, SubArea,
                          planRoute, sampleSchedule)

frame = LocalFrame(GeoPoint(40.30, 23.00))


def squareAround(centerEast: float, centerNorth: float, sideMeters: float) -> list[GeoPoint]:
    """
    @brief Four GPS corners of a square given by its planar center and side length.
    """
    half = sideMeters / 2
    corners = [(centerEast - half, centerNorth - half), (centerEast + half, centerNorth - half),
               (centerEast + half, centerNorth + half), (centerEast - half, centerNorth + half)]
    return [frame.toGeographic(corner) for corner in corners]


def printPlan(title: str, plan: RoutePlan) -> None:
    print(f"--- {title}")
    print(f"status={plan.status.value} covered={plan.coveredSubAreaIdentifiers} "
          f"skipped={plan.skippedReasons}")
    print(f"total={plan.totalDistanceMeters / 1000:.1f} km "
          f"(transit {plan.transitDistanceMeters / 1000:.1f}, "
          f"sampling {plan.samplingDistanceMeters / 1000:.1f}), "
          f"duration={plan.estimatedDurationSeconds / 3600:.1f} h, "
          f"samples~{plan.estimatedSampleCount}, waypoints={len(plan.waypoints)}")
    if plan.warnings:
        print("warnings:", plan.warnings)


area = [frame.toGeographic(corner) for corner in
        [(-2000, -2000), (2000, -2000), (2000, 2000), (-2000, 2000)]]
subAreas = [
    SubArea("farmA", squareAround(-1200, -1200, 700)),
    SubArea("farmB", squareAround(900, -1300, 316)),
    SubArea("farmC", squareAround(-800, 300, 1000)),
    SubArea("farmD", squareAround(1100, 600, 500)),
    SubArea("farmE", squareAround(-300, 1500, 400)),
    SubArea("farmF", squareAround(400, -200, 300)),
]
startPoint = frame.toGeographic((-1900, -1900))

if __name__ == "__main__":
    printPlan("default budget (30 km)", planRoute(area, subAreas, startPoint))
    printPlan("tight budget (15 km)",
              planRoute(area, subAreas, startPoint, budget=Budget(maxDistanceMeters=15000)))
    printPlan("bigger buffer (200 m)",
              planRoute(area, subAreas, startPoint, sampling=SamplingSpec(bufferMeters=200)))
    printPlan("explicit end point (north-east corner)",
              planRoute(area, subAreas, startPoint, endPoint=frame.toGeographic((1900, 1900))))
    printPlan("start too far", planRoute(area, subAreas, frame.toGeographic((-6000, 0))))
    printPlan("energy-derived budget (2178 Wh, 165 W)",
              planRoute(area, subAreas, startPoint, budget=Budget.fromEnergy(2178, 165)))

    edgeSubAreas = subAreas + [SubArea("farmEdge", squareAround(1900, -1900, 150))]
    edgePlan = planRoute(area, edgeSubAreas, startPoint)
    printPlan("farm whose ring leaves the area (planned in full, with a warning)", edgePlan)
    print(f"sample points at 5 m spacing: {len(sampleSchedule(edgePlan, 5.0))}")
