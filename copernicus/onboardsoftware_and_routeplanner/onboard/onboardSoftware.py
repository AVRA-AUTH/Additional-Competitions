"""
@file onboardSoftware.py
@brief Top level of the onboard software: one step() call per simulator tick (FUN-1).
"""
from __future__ import annotations

from typing import Optional, Sequence

from onboard.commandHandler import CommandHandler
from onboard.driftAssessor import DriftAssessor
from onboard.energyAssessor import EnergyAssessor
from onboard.healthMonitor import HealthMonitor, HealthReport
from onboard.modeManager import ModeContext, ModeManager
from onboard.modeProfile import isFunctionEnabled
from onboard.onboardConfiguration import OnboardConfiguration
from onboard.onboardTypes import (FunctionName, OperationalMode, RerouteCommand, Telecommand,
                                  TransitionSource, VehicleInputs, VehicleOutputs)
from onboard.reporter import Reporter
from onboard.routeExecutor import GuidanceCommand, RouteExecutor
from onboard.sampleManager import SampleManager


class OnboardSoftware:
    """
    @brief Runs the onboard functions in a fixed order every tick.
    @details Order inside step(): health, telecommands, autonomous mode transitions, route
             execution and sampling (nominal only), end-of-route transition, telemetry.
    @param configuration Onboard configuration, defaults are used if omitted.
    @param initialMode Mode at power-up.
    """

    def __init__(self, configuration: Optional[OnboardConfiguration] = None,
                 initialMode: OperationalMode = OperationalMode.STANDBY):
        self.configuration = configuration or OnboardConfiguration()
        self.healthMonitor = HealthMonitor()
        self.energyAssessor = EnergyAssessor(self.configuration)
        self.driftAssessor = DriftAssessor(self.configuration)
        self.modeManager = ModeManager(self.configuration, initialMode)
        self.routeExecutor = RouteExecutor(self.configuration)
        self.sampleManager = SampleManager(self.configuration)
        self.reporter = Reporter(self.configuration)
        self.commandHandler = CommandHandler(self.modeManager, self.routeExecutor, self.reporter)

    @property
    def mode(self) -> OperationalMode:
        return self.modeManager.mode

    def buildContext(self, inputs: VehicleInputs, healthReport: HealthReport) -> ModeContext:
        """
        @brief Collects the facts the mode manager decides on.
        """
        remainingDistance = self.routeExecutor.remainingDistanceMeters(inputs.position)
        drift = self.driftAssessor.calculateDrift(inputs)
        return ModeContext(
            stateOfCharge=inputs.batteryStateOfCharge, healthReport=healthReport,
            isDriftAcceptable=self.driftAssessor.isDriftAcceptable(drift),
            hasPendingRoute=self.routeExecutor.hasPendingRoute,
            isRouteFeasible=self.energyAssessor.canCompleteDistance(
                inputs.batteryStateOfCharge, remainingDistance),
            isRouteComplete=self.routeExecutor.isRouteComplete)

    def applyAutonomousTransition(self, inputs: VehicleInputs, healthReport: HealthReport) -> None:
        """
        @brief Applies the autonomous transition that is due, if the guards accept it.
        """
        context = self.buildContext(inputs, healthReport)
        dueTransition = self.modeManager.evaluateAutonomousTransition(context)
        if dueTransition is not None:
            targetMode, reason = dueTransition
            self.modeManager.requestTransition(targetMode, TransitionSource.AUTONOMOUS, context,
                                               inputs.timeSeconds, reason)

    def processTelecommands(self, telecommands: Sequence[Telecommand], inputs: VehicleInputs,
                            healthReport: HealthReport, outputs: VehicleOutputs) -> None:
        """
        @brief Handles the telecommands in order and collects the responses.
        @details A reroute that arrives in nominal mode and is not energy-feasible moves the ASV to
                 standby, because OPS-14 forbids executing it.
        """
        for command in telecommands:
            context = self.buildContext(inputs, healthReport)
            response, frame = self.commandHandler.handle(command, context, inputs, healthReport)
            outputs.commandResponses.append(response)
            if frame is not None:
                outputs.telemetryFrames.append(frame)
            if isinstance(command, RerouteCommand) and response.isAccepted:
                context = self.buildContext(inputs, healthReport)
                if (self.mode == OperationalMode.NOMINAL and context.hasPendingRoute
                        and not context.isRouteFeasible):
                    self.modeManager.requestTransition(
                        OperationalMode.STANDBY, TransitionSource.AUTONOMOUS, context,
                        inputs.timeSeconds, "OPS-14: updated route not feasible")

    def runRouteFunctions(self, inputs: VehicleInputs) -> Optional[GuidanceCommand]:
        """
        @brief Runs route execution and sampling if the current mode allows them.
        """
        if not isFunctionEnabled(self.mode, FunctionName.ROUTE_EXECUTION):
            self.sampleManager.interrupt()
            return None
        guidance = self.routeExecutor.step(inputs.position)
        if guidance is not None and isFunctionEnabled(self.mode, FunctionName.SAMPLING):
            self.sampleManager.update(inputs.timeSeconds, inputs.position,
                                      guidance.travelledLegIsSampling, inputs.payloadReadings)
        return guidance

    def step(self, inputs: VehicleInputs,
             telecommands: Sequence[Telecommand] = ()) -> VehicleOutputs:
        """
        @brief Runs one tick of the onboard software.
        @param inputs Vehicle readings for this tick.
        @param telecommands Telecommands received since the previous tick.
        @return Propulsion demand, telemetry frames, command responses and mode transitions.
        """
        healthReport = self.healthMonitor.evaluate(inputs)
        outputs = VehicleOutputs(mode=self.mode)
        self.processTelecommands(telecommands, inputs, healthReport, outputs)
        self.applyAutonomousTransition(inputs, healthReport)
        guidance = self.runRouteFunctions(inputs)
        if self.mode == OperationalMode.NOMINAL and self.routeExecutor.isRouteComplete:
            self.applyAutonomousTransition(inputs, healthReport)
        mode = self.mode
        if (guidance is not None and mode == OperationalMode.NOMINAL
                and isFunctionEnabled(mode, FunctionName.PROPULSION)
                and self.routeExecutor.hasPendingRoute):
            outputs.propulsionEnabled = True
            outputs.desiredHeadingDegrees = guidance.desiredHeadingDegrees
            outputs.desiredSpeedMetersPerSecond = guidance.desiredSpeedMetersPerSecond
            outputs.crossTrackErrorMeters = guidance.crossTrackErrorMeters
        if mode == OperationalMode.SAFE:
            outputs.isolatedSubsystems = healthReport.isolatedSubsystems
        if isFunctionEnabled(mode, FunctionName.TELEMETRY):
            frame = self.reporter.periodicFrame(inputs, mode, healthReport, self.sampleManager)
            if frame is not None:
                outputs.telemetryFrames.append(frame)
        outputs.mode = mode
        outputs.modeTransitions = self.modeManager.takeNewTransitions()
        return outputs
