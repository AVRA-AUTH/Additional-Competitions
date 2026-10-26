"""
@file testOnboard.py
@brief Automated checks for the onboard software. Run with: python3 -m unittest -v testOnboard
"""
import unittest

from onboard.onboardConfiguration import OnboardConfiguration
from onboard.onboardGeometry import distanceBetweenPositions, fromPlanarMeters
from onboard.modeProfile import isFunctionEnabled
from onboard.onboardSoftware import OnboardSoftware
from onboard.onboardTypes import (DataRequestCommand, FunctionName, GeographicPosition,
                                  ModeChangeCommand, OperationalMode, RerouteCommand,
                                  RouteWaypoint, VehicleInputs)
from routePlanner import GeoPoint, LocalFrame, SubArea, planRoute
from simulatorExample import SimpleVesselSimulator, convertPlanToRoute, runSimulation

origin = GeographicPosition(40.30, 23.00)


def makeInputs(timeSeconds=0.0, **overrides) -> VehicleInputs:
    values = dict(timeSeconds=timeSeconds, position=origin, headingDegrees=0.0,
                  speedMetersPerSecond=0.0, hasGpsFix=True, batteryStateOfCharge=0.9,
                  batteryCurrentAmperes=5.0, batteryTemperatureCelsius=25.0, chargingPowerWatts=0.0,
                  thrusterIsOperational=True, communicationLinkIsUp=True,
                  simulatedDriftMetersPerSecond=0.1,
                  payloadReadings={"chlorophyll": 0.3})
    values.update(overrides)
    return VehicleInputs(**values)


def makeRoute(*legs):
    """
    @brief Builds a straight northbound route from (northMeters, isSamplingLeg) pairs.
    """
    return tuple(RouteWaypoint(fromPlanarMeters(origin, 0.0, northMeters), isSamplingLeg)
                 for northMeters, isSamplingLeg in legs)


shortRoute = makeRoute((0, False), (100, False), (300, True), (400, False))
farAwayRoute = makeRoute((0, False), (40000, True))


def makeSoftware(initialMode, route=()):
    software = OnboardSoftware(initialMode=initialMode)
    if route:
        software.routeExecutor.loadRoute(list(route))
    return software


class ModeLogicTest(unittest.TestCase):

    def testModeProfilesMatchRequirements(self):
        for function in FunctionName:
            self.assertTrue(isFunctionEnabled(OperationalMode.NOMINAL, function))
        for mode in (OperationalMode.STANDBY, OperationalMode.SAFE):
            self.assertFalse(isFunctionEnabled(mode, FunctionName.PROPULSION))
            self.assertFalse(isFunctionEnabled(mode, FunctionName.ROUTE_EXECUTION))
            self.assertFalse(isFunctionEnabled(mode, FunctionName.SAMPLING))
            self.assertTrue(isFunctionEnabled(mode, FunctionName.TELEMETRY))
            self.assertTrue(isFunctionEnabled(mode, FunctionName.TELECOMMAND))
            self.assertTrue(isFunctionEnabled(mode, FunctionName.NAVIGATION))

    def testLowBatteryWithAcceptableDriftEntersStandby(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        outputs = software.step(makeInputs(batteryStateOfCharge=0.35, simulatedDriftMetersPerSecond=0.1))
        self.assertEqual(outputs.mode, OperationalMode.STANDBY)
        self.assertEqual(outputs.modeTransitions[0].reason, "lowBattery")

    def testLowBatteryWithUnacceptableDriftStaysNominal(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        outputs = software.step(makeInputs(batteryStateOfCharge=0.35, simulatedDriftMetersPerSecond=0.9))
        self.assertEqual(outputs.mode, OperationalMode.NOMINAL)

    def testBatteryBelowSafeThresholdEntersSafeRegardlessOfDrift(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        outputs = software.step(makeInputs(batteryStateOfCharge=0.15, simulatedDriftMetersPerSecond=0.9))
        self.assertEqual(outputs.mode, OperationalMode.SAFE)

    def testFaultEntersSafeAndIsolatesOnlyTheFaultedSubsystem(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        outputs = software.step(makeInputs(thrusterIsOperational=False))
        self.assertEqual(outputs.mode, OperationalMode.SAFE)
        self.assertEqual(outputs.isolatedSubsystems, frozenset({"propulsion"}))
        self.assertEqual(len(outputs.telemetryFrames), 1)

    def testThrusterFaultDoesNotPushStandbyIntoSafe(self):
        software = makeSoftware(OperationalMode.STANDBY)
        outputs = software.step(makeInputs(thrusterIsOperational=False))
        self.assertEqual(outputs.mode, OperationalMode.STANDBY)

    def testBatteryOvercurrentPushesStandbyIntoSafe(self):
        software = makeSoftware(OperationalMode.STANDBY)
        outputs = software.step(makeInputs(batteryCurrentAmperes=75.0))
        self.assertEqual(outputs.mode, OperationalMode.SAFE)
        self.assertEqual(outputs.isolatedSubsystems, frozenset({"battery"}))

    def testSafeModeIsNeverLeftAutonomously(self):
        software = makeSoftware(OperationalMode.SAFE, shortRoute)
        for tick in range(5):
            outputs = software.step(makeInputs(timeSeconds=tick * 10.0, batteryStateOfCharge=1.0))
        self.assertEqual(outputs.mode, OperationalMode.SAFE)

    def testSafeToNominalDeniedWhileThrusterFails(self):
        software = makeSoftware(OperationalMode.SAFE)
        outputs = software.step(makeInputs(thrusterIsOperational=False),
                                [ModeChangeCommand(OperationalMode.NOMINAL)])
        self.assertFalse(outputs.commandResponses[0].isAccepted)
        self.assertIn("propulsion", outputs.commandResponses[0].reason)
        self.assertEqual(outputs.mode, OperationalMode.SAFE)

    def testSafeToStandbyAcceptedWhileOnlyThrusterFails(self):
        software = makeSoftware(OperationalMode.SAFE)
        outputs = software.step(makeInputs(thrusterIsOperational=False),
                                [ModeChangeCommand(OperationalMode.STANDBY)])
        self.assertTrue(outputs.commandResponses[0].isAccepted)
        self.assertEqual(outputs.mode, OperationalMode.STANDBY)

    def testSafeToStandbyDeniedWhileGpsFails(self):
        software = makeSoftware(OperationalMode.SAFE)
        outputs = software.step(makeInputs(hasGpsFix=False),
                                [ModeChangeCommand(OperationalMode.STANDBY)])
        self.assertFalse(outputs.commandResponses[0].isAccepted)
        self.assertEqual(outputs.mode, OperationalMode.SAFE)

    def testSafeEnteredForLowBatteryNeedsSeventyPercentToLeave(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        software.step(makeInputs(batteryStateOfCharge=0.15))
        self.assertEqual(software.mode, OperationalMode.SAFE)
        denied = software.step(makeInputs(timeSeconds=10.0, batteryStateOfCharge=0.5),
                               [ModeChangeCommand(OperationalMode.STANDBY)])
        self.assertFalse(denied.commandResponses[0].isAccepted)
        accepted = software.step(makeInputs(timeSeconds=20.0, batteryStateOfCharge=0.72),
                                 [ModeChangeCommand(OperationalMode.STANDBY)])
        self.assertTrue(accepted.commandResponses[0].isAccepted)
        self.assertEqual(software.mode, OperationalMode.STANDBY)

    def testSafeEnteredByTelecommandHasNoBatteryRequirement(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        software.step(makeInputs(), [ModeChangeCommand(OperationalMode.SAFE)])
        outputs = software.step(makeInputs(timeSeconds=10.0, batteryStateOfCharge=0.5),
                                [ModeChangeCommand(OperationalMode.STANDBY)])
        self.assertTrue(outputs.commandResponses[0].isAccepted)

    def testStandbyReturnsToNominalAutonomouslyAtSeventyPercent(self):
        software = makeSoftware(OperationalMode.NOMINAL, shortRoute)
        software.step(makeInputs(batteryStateOfCharge=0.35))
        self.assertEqual(software.mode, OperationalMode.STANDBY)
        software.step(makeInputs(timeSeconds=10.0, batteryStateOfCharge=0.6))
        self.assertEqual(software.mode, OperationalMode.STANDBY)
        software.step(makeInputs(timeSeconds=20.0, batteryStateOfCharge=0.72))
        self.assertEqual(software.mode, OperationalMode.NOMINAL)

    def testStandbyEnteredByTelecommandDoesNotReturnAutonomously(self):
        software = makeSoftware(OperationalMode.NOMINAL, shortRoute)
        software.step(makeInputs(), [ModeChangeCommand(OperationalMode.STANDBY)])
        software.step(makeInputs(timeSeconds=10.0, batteryStateOfCharge=1.0))
        self.assertEqual(software.mode, OperationalMode.STANDBY)

    def testNoAutonomousReturnWithoutPendingRoute(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        software.step(makeInputs(batteryStateOfCharge=0.35))
        software.step(makeInputs(timeSeconds=10.0, batteryStateOfCharge=0.9))
        self.assertEqual(software.mode, OperationalMode.STANDBY)

    def testNominalDeniedWhenRouteIsNotEnergyFeasible(self):
        software = makeSoftware(OperationalMode.STANDBY, farAwayRoute)
        outputs = software.step(makeInputs(batteryStateOfCharge=0.5),
                                [ModeChangeCommand(OperationalMode.NOMINAL)])
        self.assertFalse(outputs.commandResponses[0].isAccepted)
        self.assertIn("OPS-14", outputs.commandResponses[0].reason)

    def testRerouteDoesNotChangeModeAndIsNotExecutedInStandby(self):
        software = makeSoftware(OperationalMode.STANDBY)
        outputs = software.step(makeInputs(), [RerouteCommand(shortRoute)])
        self.assertTrue(outputs.commandResponses[0].isAccepted)
        self.assertIn("not executed", outputs.commandResponses[0].reason)
        self.assertEqual(outputs.mode, OperationalMode.STANDBY)
        self.assertFalse(outputs.propulsionEnabled)

    def testInfeasibleRerouteInNominalMovesToStandby(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        outputs = software.step(makeInputs(batteryStateOfCharge=0.5), [RerouteCommand(farAwayRoute)])
        self.assertEqual(outputs.mode, OperationalMode.STANDBY)
        self.assertFalse(outputs.propulsionEnabled)

    def testCalculateDriftReadsTheSimulatorValue(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        self.assertEqual(software.driftAssessor.calculateDrift(
            makeInputs(simulatedDriftMetersPerSecond=0.37)), 0.37)


class TelemetryAndCommandTest(unittest.TestCase):

    def framesOverTime(self, mode):
        software = makeSoftware(mode)
        frames = []
        for tick in range(0, 1900, 10):
            frames.extend(software.step(makeInputs(timeSeconds=float(tick))).telemetryFrames)
        return frames

    def testTelemetryEveryTenMinutesInEveryMode(self):
        for mode in OperationalMode:
            frames = self.framesOverTime(mode)
            self.assertEqual([frame.timeSeconds for frame in frames], [0.0, 600.0, 1200.0, 1800.0],
                             msg=mode.value)

    def testSafeFrameIsReduced(self):
        frame = self.framesOverTime(OperationalMode.SAFE)[0]
        self.assertTrue(frame.isReduced)
        self.assertEqual(frame.payloadReadings, {})
        self.assertEqual(frame.samples, [])

    def testNominalFrameCarriesPayloadReadings(self):
        frame = self.framesOverTime(OperationalMode.NOMINAL)[0]
        self.assertFalse(frame.isReduced)
        self.assertIn("chlorophyll", frame.payloadReadings)

    def testDataRequestAnsweredInSafeMode(self):
        software = makeSoftware(OperationalMode.SAFE)
        outputs = software.step(makeInputs(timeSeconds=5.0),
                                [DataRequestCommand(("batteryStateOfCharge",))])
        self.assertTrue(outputs.commandResponses[0].isAccepted)
        onDemand = [frame for frame in outputs.telemetryFrames if frame.isOnDemand][0]
        self.assertEqual(list(onDemand.channelValues), ["batteryStateOfCharge"])

    def testUnknownChannelRequestIsRejected(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        outputs = software.step(makeInputs(), [DataRequestCommand(("doesNotExist",))])
        self.assertFalse(outputs.commandResponses[0].isAccepted)

    def testEmptyRerouteIsRejected(self):
        software = makeSoftware(OperationalMode.NOMINAL)
        outputs = software.step(makeInputs(), [RerouteCommand(())])
        self.assertFalse(outputs.commandResponses[0].isAccepted)


class RouteExecutionTest(unittest.TestCase):

    def runShortRoute(self):
        simulator = SimpleVesselSimulator(origin)
        software = makeSoftware(OperationalMode.STANDBY, shortRoute)
        result = runSimulation(software, simulator, 2000.0, 5.0,
                               scheduledCommands=[(0.0, ModeChangeCommand(OperationalMode.NOMINAL))],
                               stopCondition=lambda outputs: software.routeExecutor.isRouteComplete)
        return software, simulator, result

    def testRouteIsFollowedWithSmallCrossTrackError(self):
        software = makeSoftware(OperationalMode.NOMINAL, shortRoute)
        simulator = SimpleVesselSimulator(origin)
        errors = []
        runSimulation(software, simulator, 2000.0, 5.0,
                      stopCondition=lambda outputs: errors.append(outputs.crossTrackErrorMeters)
                      or software.routeExecutor.isRouteComplete)
        self.assertTrue(software.routeExecutor.isRouteComplete)
        self.assertLess(max(abs(error) for error in errors if error is not None), 1.0)

    def testRouteCompletionReturnsToStandby(self):
        software, simulator, result = self.runShortRoute()
        self.assertEqual(software.mode, OperationalMode.STANDBY)
        self.assertEqual(result.transitions[-1].reason, "routeComplete")
        self.assertFalse(result.finalOutputs.propulsionEnabled)

    def testSamplesAreTakenOnlyOnTheSamplingLeg(self):
        software, simulator, result = self.runShortRoute()
        samples = software.sampleManager.unsentSamples
        self.assertAlmostEqual(len(samples), 40, delta=2)
        for sample in samples:
            self.assertGreaterEqual(distanceBetweenPositions(origin, sample.position), 99.0)
            self.assertLessEqual(distanceBetweenPositions(origin, sample.position), 301.0)

    def testSampleSpacingIsRespected(self):
        software, simulator, result = self.runShortRoute()
        samples = software.sampleManager.unsentSamples
        for first, second in zip(samples, samples[1:]):
            self.assertAlmostEqual(distanceBetweenPositions(first.position, second.position),
                                   OnboardConfiguration().sampleSpacingMeters, delta=0.5)

    def testPausedRouteResumesWhereItStopped(self):
        software = makeSoftware(OperationalMode.NOMINAL, shortRoute)
        simulator = SimpleVesselSimulator(origin)
        runSimulation(software, simulator, 100.0, 5.0)
        progressBeforePause = software.routeExecutor.activeIndex
        software.step(simulator.currentInputs(), [ModeChangeCommand(OperationalMode.STANDBY)])
        software.step(simulator.currentInputs())
        self.assertEqual(software.routeExecutor.activeIndex, progressBeforePause)


class MissionWithPlannerTest(unittest.TestCase):

    def testFullMissionFromPlannerRoute(self):
        frame = LocalFrame(GeoPoint(40.30, 23.00))
        area = [frame.toGeographic(corner) for corner in
                [(-1000, -1000), (1000, -1000), (1000, 1000), (-1000, 1000)]]
        half = 100.0
        farm = [frame.toGeographic(corner) for corner in
                [(-half, -half), (half, -half), (half, half), (-half, half)]]
        plan = planRoute(area, [SubArea("farm", farm)], frame.toGeographic((-900, -900)))
        route = convertPlanToRoute(plan)
        simulator = SimpleVesselSimulator(GeographicPosition(plan.waypoints[0].latitude,
                                                              plan.waypoints[0].longitude))
        software = OnboardSoftware(initialMode=OperationalMode.STANDBY)
        result = runSimulation(
            software, simulator, 10000.0, 10.0,
            scheduledCommands=[(0.0, RerouteCommand(route)),
                               (10.0, ModeChangeCommand(OperationalMode.NOMINAL))],
            stopCondition=lambda outputs: software.routeExecutor.isRouteComplete)
        self.assertEqual(software.mode, OperationalMode.STANDBY)
        self.assertAlmostEqual(software.sampleManager.totalSampleCount, plan.estimatedSampleCount,
                               delta=0.05 * plan.estimatedSampleCount)
        self.assertEqual([record.toMode for record in result.transitions],
                         [OperationalMode.NOMINAL, OperationalMode.STANDBY])


if __name__ == "__main__":
    unittest.main()
