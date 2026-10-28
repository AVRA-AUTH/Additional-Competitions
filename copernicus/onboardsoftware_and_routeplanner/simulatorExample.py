"""
@file simulatorExample.py
@brief A very simple vessel simulator and a simulation loop to run the onboard software.
@details This is an example of the interface the real simulator has to provide: every tick it
         turns the onboard outputs into the next VehicleInputs. Replace SimpleVesselSimulator
         with the real simulator and keep runSimulation() as the loop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from onboard.onboardTypes import (CommandResponse, GeographicPosition, ModeTransitionRecord,
                                  OperationalMode, RouteWaypoint, Telecommand, TelemetryFrame,
                                  VehicleInputs, VehicleOutputs)
from routePlanner import RoutePlan

metersPerDegreeLatitude = math.radians(1.0) * 6371000.0
secondsPerHour = 3600.0
batteryNominalVoltage = 14.8


def convertPlanToRoute(plan: RoutePlan) -> tuple[RouteWaypoint, ...]:
    """
    @brief Converts the off-board planner result into the route message the onboard side expects.
    @details This is the only place where the planner and the onboard software meet.
    """
    return tuple(RouteWaypoint(GeographicPosition(waypoint.latitude, waypoint.longitude),
                               waypoint.isSamplingLeg) for waypoint in plan.waypoints)


class SimpleVesselSimulator:
    """
    @brief Point-mass vessel with a battery, solar charging, drift and injectable faults.
    @param startPosition Initial position.
    @param stateOfCharge Initial battery state of charge (0 to 1).
    @param solarPowerWatts Constant charging power.
    @param driftMetersPerSecond Drift value reported to the onboard software, also moves the
           vessel east whenever propulsion is off. Change it at any time.
    """

    def __init__(self, startPosition: GeographicPosition, stateOfCharge: float = 1.0,
                 batteryCapacityWattHours: float = 2178.0, propulsionPowerWatts: float = 165.0,
                 hotelPowerWatts: float = 20.0, solarPowerWatts: float = 0.0,
                 driftMetersPerSecond: float = 0.0,
                 payloadProvider: Optional[Callable[[float, float, float], dict[str, float]]] = None):
        self.position = startPosition
        self.stateOfCharge = stateOfCharge
        self.batteryCapacityWattHours = batteryCapacityWattHours
        self.propulsionPowerWatts = propulsionPowerWatts
        self.hotelPowerWatts = hotelPowerWatts
        self.solarPowerWatts = solarPowerWatts
        self.driftMetersPerSecond = driftMetersPerSecond
        self.timeSeconds = 0.0
        self.headingDegrees = 0.0
        self.speedMetersPerSecond = 0.0
        self.powerWatts = hotelPowerWatts
        self.hasGpsFix = True
        self.thrusterIsOperational = True
        self.communicationLinkIsUp = True
        self.batteryTemperatureCelsius = 25.0
        self.payloadProvider = payloadProvider

    def movePosition(self, eastMeters: float, northMeters: float) -> None:
        """
        @brief Moves the vessel by a planar displacement.
        """
        metersPerDegreeLongitude = metersPerDegreeLatitude * math.cos(math.radians(self.position.latitude))
        self.position = GeographicPosition(self.position.latitude + northMeters / metersPerDegreeLatitude,
                                           self.position.longitude + eastMeters / metersPerDegreeLongitude)

    def step(self, outputs: Optional[VehicleOutputs], timeStepSeconds: float) -> VehicleInputs:
        """
        @brief Advances the simulation by one time step using the latest onboard outputs.
        @param outputs Onboard outputs of the previous tick, None before the first tick.
        @return The vehicle inputs for the next onboard tick.
        """
        propulsionOn = bool(outputs and outputs.propulsionEnabled and self.thrusterIsOperational)
        if propulsionOn:
            self.headingDegrees = outputs.desiredHeadingDegrees
            self.speedMetersPerSecond = outputs.desiredSpeedMetersPerSecond
            heading = math.radians(self.headingDegrees)
            distance = self.speedMetersPerSecond * timeStepSeconds
            self.movePosition(distance * math.sin(heading), distance * math.cos(heading))
        else:
            self.speedMetersPerSecond = 0.0
            self.movePosition(self.driftMetersPerSecond * timeStepSeconds, 0.0)
        self.powerWatts = self.propulsionPowerWatts if propulsionOn else self.hotelPowerWatts
        netPowerWatts = self.powerWatts - self.solarPowerWatts
        energyChange = netPowerWatts * timeStepSeconds / secondsPerHour / self.batteryCapacityWattHours
        self.stateOfCharge = min(1.0, max(0.0, self.stateOfCharge - energyChange))
        self.timeSeconds += timeStepSeconds
        return self.currentInputs()

    def currentInputs(self) -> VehicleInputs:
        """
        @brief The vehicle readings at the current simulation time.
        """
        if self.payloadProvider is None:
            # A neutral constant keeps legacy onboard demos deterministic; mission runs inject
            # the CSV-backed stand-in or a real sensor provider explicitly.
            payloadReadings = {"chlorophyll": 0.5, "seaSurfaceTemperature": 20.0}
        else:
            payloadReadings = dict(self.payloadProvider(
                self.position.latitude, self.position.longitude, self.timeSeconds))
        return VehicleInputs(
            timeSeconds=self.timeSeconds, position=self.position, headingDegrees=self.headingDegrees,
            speedMetersPerSecond=self.speedMetersPerSecond, hasGpsFix=self.hasGpsFix,
            batteryStateOfCharge=self.stateOfCharge,
            batteryCurrentAmperes=self.powerWatts / batteryNominalVoltage,
            batteryTemperatureCelsius=self.batteryTemperatureCelsius,
            chargingPowerWatts=self.solarPowerWatts, thrusterIsOperational=self.thrusterIsOperational,
            communicationLinkIsUp=self.communicationLinkIsUp,
            simulatedDriftMetersPerSecond=self.driftMetersPerSecond,
            payloadReadings=payloadReadings)


@dataclass
class SimulationResult:
    """
    @brief Everything collected during one simulation run.
    """
    transitions: list[ModeTransitionRecord] = field(default_factory=list)
    telemetryFrames: list[TelemetryFrame] = field(default_factory=list)
    commandResponses: list[CommandResponse] = field(default_factory=list)
    finalInputs: Optional[VehicleInputs] = None
    finalOutputs: Optional[VehicleOutputs] = None
    tickCount: int = 0


def runSimulation(software, simulator: SimpleVesselSimulator, durationSeconds: float,
                  timeStepSeconds: float = 10.0,
                  scheduledCommands: Sequence[tuple[float, Telecommand]] = (),
                  scheduledActions: Sequence[tuple[float, Callable[[SimpleVesselSimulator], None]]] = (),
                  stopCondition: Optional[Callable[[VehicleOutputs], bool]] = None) -> SimulationResult:
    """
    @brief Runs the onboard software against a simulator.
    @details Every tick: scheduled actions change the simulator (for example inject a fault),
             the simulator produces the inputs, the onboard software returns the outputs, and the
             outputs are fed to the simulator on the next tick.
    @param scheduledCommands Pairs of (time in seconds, telecommand) sent when the time is reached.
    @param scheduledActions Pairs of (time in seconds, function that changes the simulator).
    @param stopCondition Optional function that ends the run early when it returns True.
    @return The collected transitions, telemetry frames and command responses.
    """
    pendingCommands = sorted(scheduledCommands, key=lambda entry: entry[0])
    pendingActions = sorted(scheduledActions, key=lambda entry: entry[0])
    result = SimulationResult()
    outputs: Optional[VehicleOutputs] = None
    inputs = simulator.currentInputs()
    while simulator.timeSeconds <= durationSeconds:
        while pendingActions and pendingActions[0][0] <= simulator.timeSeconds:
            pendingActions.pop(0)[1](simulator)
            inputs = simulator.currentInputs()
        dueCommands = []
        while pendingCommands and pendingCommands[0][0] <= simulator.timeSeconds:
            dueCommands.append(pendingCommands.pop(0)[1])
        outputs = software.step(inputs, dueCommands)
        result.transitions.extend(outputs.modeTransitions)
        result.telemetryFrames.extend(outputs.telemetryFrames)
        result.commandResponses.extend(outputs.commandResponses)
        result.tickCount += 1
        result.finalInputs, result.finalOutputs = inputs, outputs
        if stopCondition and stopCondition(outputs):
            break
        inputs = simulator.step(outputs, timeStepSeconds)
    return result
