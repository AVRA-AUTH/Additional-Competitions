"""
@file testRoutePlanner.py
@brief Automated checks for routePlanner.py. Run with: python3 -m unittest -v testRoutePlanner
"""
import unittest

from routePlanner import (Budget, GeoPoint, LocalFrame, RouteStatus, SamplingSpec, SubArea,
                          lengthInsideConvex, planRoute, sampleSchedule,
                          sortCounterClockwiseConvex)

frame = LocalFrame(GeoPoint(40.30, 23.00))


def squareAround(centerEast, centerNorth, sideMeters):
    half = sideMeters / 2
    corners = [(centerEast - half, centerNorth - half), (centerEast + half, centerNorth - half),
               (centerEast + half, centerNorth + half), (centerEast - half, centerNorth + half)]
    return [frame.toGeographic(corner) for corner in corners]


area = [frame.toGeographic(corner) for corner in
        [(-2000, -2000), (2000, -2000), (2000, 2000), (-2000, 2000)]]
startPoint = frame.toGeographic((-1900, -1900))


class RoutePlannerTest(unittest.TestCase):

    def testSingleFarmRingLengthAndStatus(self):
        plan = planRoute(area, [SubArea("farm", squareAround(0, 0, 400))], startPoint)
        self.assertEqual(plan.status, RouteStatus.COMPLETE)
        expectedRingMeters = 4 * (400 + 2 * 50)
        self.assertAlmostEqual(plan.samplingDistanceMeters, expectedRingMeters, delta=1.0)

    def testRouteStartsAndEndsAtGivenPoints(self):
        endPoint = frame.toGeographic((1900, 1900))
        plan = planRoute(area, [SubArea("farm", squareAround(0, 0, 400))], startPoint, endPoint)
        self.assertEqual(plan.waypoints[0].kind, "start")
        self.assertEqual(plan.waypoints[-1].kind, "end")
        self.assertAlmostEqual(plan.waypoints[-1].latitude, endPoint.latitude, places=6)
        self.assertAlmostEqual(plan.waypoints[-1].longitude, endPoint.longitude, places=6)

    def testWithoutEndPointRouteEndsAtLastRingExit(self):
        plan = planRoute(area, [SubArea("farm", squareAround(0, 0, 400))], startPoint)
        lastWaypoint = plan.waypoints[-1]
        self.assertEqual(lastWaypoint.kind, "ringExit")
        self.assertTrue(lastWaypoint.isSamplingLeg)
        self.assertNotIn("end", [waypoint.kind for waypoint in plan.waypoints])
        entryWaypoint = next(waypoint for waypoint in plan.waypoints if waypoint.kind == "ringEntry")
        self.assertAlmostEqual(lastWaypoint.latitude, entryWaypoint.latitude, places=8)
        self.assertAlmostEqual(lastWaypoint.longitude, entryWaypoint.longitude, places=8)

    def testWithoutEndPointIsShorterThanReturningToStart(self):
        subAreas = [SubArea("first", squareAround(-1200, -1200, 400)),
                    SubArea("second", squareAround(1200, 1200, 400))]
        openEnded = planRoute(area, subAreas, startPoint)
        returning = planRoute(area, subAreas, startPoint, endPoint=startPoint)
        self.assertLess(openEnded.totalDistanceMeters, returning.totalDistanceMeters)

    def testBudgetDropsFarmsAndMarksPartial(self):
        subAreas = [SubArea("near", squareAround(-1200, -1200, 400)),
                    SubArea("far", squareAround(1200, 1200, 400))]
        plan = planRoute(area, subAreas, startPoint, budget=Budget(maxDistanceMeters=4000))
        self.assertEqual(plan.status, RouteStatus.PARTIAL)
        self.assertEqual(plan.skippedReasons.get("far"), "exceedsBudget")
        self.assertLessEqual(plan.totalDistanceMeters, 4000)

    def testStartTooFarIsInfeasible(self):
        plan = planRoute(area, [SubArea("farm", squareAround(0, 0, 400))],
                         frame.toGeographic((-6000, 0)))
        self.assertEqual(plan.status, RouteStatus.INFEASIBLE)
        self.assertTrue(plan.warnings)

    def testSubAreaOutsideAreaIsSkippedWithWarning(self):
        subAreas = [SubArea("inside", squareAround(0, 0, 400)),
                    SubArea("outside", squareAround(3500, 0, 400))]
        plan = planRoute(area, subAreas, startPoint)
        self.assertEqual(plan.skippedReasons.get("outside"), "outsideArea")
        self.assertTrue(any("outside" in warning for warning in plan.warnings))
        self.assertNotIn("outside", plan.coveredSubAreaIdentifiers)

    def testRingLeavingAreaIsPlannedWithWarning(self):
        plan = planRoute(area, [SubArea("edge", squareAround(1900, -1900, 150))], startPoint)
        self.assertEqual(plan.status, RouteStatus.COMPLETE)
        self.assertIn("edge", plan.coveredSubAreaIdentifiers)
        self.assertTrue(any("extends outside" in warning for warning in plan.warnings))

    def testCornerOrderDoesNotMatter(self):
        corners = squareAround(0, 0, 400)
        shuffled = [corners[2], corners[0], corners[3], corners[1]]
        first = planRoute(area, [SubArea("farm", corners)], startPoint)
        second = planRoute(area, [SubArea("farm", shuffled)], startPoint)
        self.assertAlmostEqual(first.totalDistanceMeters, second.totalDistanceMeters, delta=0.5)

    def testTransitNeverCrossesAFarm(self):
        subAreas = [SubArea("wall", squareAround(0, 0, 1200)),
                    SubArea("small", squareAround(-1200, 1200, 200))]
        plan = planRoute(area, subAreas, startPoint, frame.toGeographic((1900, 1900)))
        polygons = [sortCounterClockwiseConvex([frame.toPlanar(corner) for corner in subArea.corners])
                    for subArea in subAreas]
        points = [(frame.toPlanar(GeoPoint(waypoint.latitude, waypoint.longitude)), waypoint)
                  for waypoint in plan.waypoints]
        for index in range(1, len(points)):
            if points[index][1].isSamplingLeg:
                continue
            for polygon in polygons:
                self.assertLess(lengthInsideConvex(points[index - 1][0], points[index][0],
                                                   polygon, 0.0), 0.5)

    def testSampleScheduleSpacing(self):
        plan = planRoute(area, [SubArea("farm", squareAround(0, 0, 400))], startPoint,
                         sampling=SamplingSpec(bufferMeters=50, sampleSpacingMeters=5))
        samples = sampleSchedule(plan, 5.0)
        self.assertAlmostEqual(len(samples), plan.samplingDistanceMeters / 5.0, delta=5)

    def testEnergyBudgetMatchesRequirement(self):
        budget = Budget.fromEnergy(2178, 165)
        self.assertAlmostEqual(budget.maxDistanceMeters / 1000, 48.9, delta=0.5)


if __name__ == "__main__":
    unittest.main()
