"""
@file commandHandler.py
@brief Validates and executes the three telecommands: mode change, data request, reroute (OPS-12).
"""
from __future__ import annotations

from typing import Optional

from onboard.healthMonitor import HealthReport
from onboard.modeManager import ModeContext, ModeManager, telecommandReason
from onboard.onboardTypes import (CommandResponse, DataRequestCommand, ModeChangeCommand,
                                  OperationalMode, RerouteCommand, TelemetryFrame, Telecommand,
                                  TransitionSource, VehicleInputs)
from onboard.reporter import Reporter
from onboard.routeExecutor import RouteExecutor


class CommandHandler:
    """
    @brief Executes telecommands against the mode manager, route executor and reporter.
    @details A reroute never changes the mode. The route is stored and simply not executed while
             the ASV cannot be in nominal mode.
    """

    def __init__(self, modeManager: ModeManager, routeExecutor: RouteExecutor, reporter: Reporter):
        self.modeManager = modeManager
        self.routeExecutor = routeExecutor
        self.reporter = reporter

    def handle(self, command: Telecommand, context: ModeContext, inputs: VehicleInputs,
               healthReport: HealthReport) -> tuple[CommandResponse, Optional[TelemetryFrame]]:
        """
        @brief Handles one telecommand.
        @return The acknowledgement or rejection and, for a data request, the answer frame.
        """
        if isinstance(command, ModeChangeCommand):
            decision = self.modeManager.requestTransition(
                command.targetMode, TransitionSource.TELECOMMAND, context, inputs.timeSeconds,
                telecommandReason)
            return CommandResponse(command, decision.isAccepted, decision.reason), None
        if isinstance(command, DataRequestCommand):
            knownNames = set(healthReport.channelValues) | set(inputs.payloadReadings)
            unknownNames = [name for name in command.requestedChannels if name not in knownNames]
            if unknownNames:
                return CommandResponse(command, False,
                                       f"unknown channels: {', '.join(unknownNames)}"), None
            frame = self.reporter.onDemandFrame(inputs, self.modeManager.mode, healthReport,
                                                command.requestedChannels)
            return CommandResponse(command, True, "data reported"), frame
        if isinstance(command, RerouteCommand):
            if not command.waypoints:
                return CommandResponse(command, False, "route has no waypoints"), None
            self.routeExecutor.loadRoute(list(command.waypoints))
            if self.modeManager.mode == OperationalMode.NOMINAL:
                return CommandResponse(command, True, "route stored"), None
            return CommandResponse(
                command, True, f"route stored, not executed in {self.modeManager.mode.value} mode"), None
        return CommandResponse(command, False, "unknown telecommand"), None
