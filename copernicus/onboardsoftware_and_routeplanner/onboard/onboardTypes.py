"""
@file onboardTypes.py
@brief Enumerations and data classes shared by the onboard software modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Union


class OperationalMode(Enum):
    NOMINAL = "nominal"
    STANDBY = "standby"
    SAFE = "safe"


class FunctionName(Enum):
    PROPULSION = "propulsion"
    ROUTE_EXECUTION = "routeExecution"
    SAMPLING = "sampling"
    TELEMETRY = "telemetry"
    TELECOMMAND = "telecommand"
    NAVIGATION = "navigation"


class SelfTest(Enum):
    POWER = "power"
    NAVIGATION = "navigation"
    PROPULSION = "propulsion"
    COMMUNICATION = "communication"


class ChannelStatus(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    FAULT = "fault"


class TransitionSource(Enum):
    AUTONOMOUS = "autonomous"
    TELECOMMAND = "telecommand"


class FaultResponse(Enum):
    REPORT_ONLY = "reportOnly"
    ENTER_SAFE_MODE_AND_ISOLATE = "enterSafeModeAndIsolate"


@dataclass(frozen=True)
class GeographicPosition:
    """
    @brief A position on the globe in decimal degrees.
    """
    latitude: float
    longitude: float


@dataclass(frozen=True)
class RouteWaypoint:
    """
    @brief One waypoint of the route received from the off-board planner.
    @param position Waypoint position.
    @param isSamplingLeg True if the leg that arrives at this waypoint is sampled.
    """
    position: GeographicPosition
    isSamplingLeg: bool = False


@dataclass
class VehicleInputs:
    """
    @brief Everything the onboard software reads from the vehicle (or a simulator) in one tick.
    @param simulatedDriftMetersPerSecond Drift with propulsion off. In a real vehicle this is
           calculated from several sensors, here the simulator supplies the result.
    @param payloadReadings Current payload sensor values, for example chlorophyll.
    """
    timeSeconds: float
    position: GeographicPosition
    headingDegrees: float
    speedMetersPerSecond: float
    hasGpsFix: bool
    batteryStateOfCharge: float
    batteryCurrentAmperes: float
    batteryTemperatureCelsius: float
    chargingPowerWatts: float
    thrusterIsOperational: bool
    communicationLinkIsUp: bool
    simulatedDriftMetersPerSecond: float
    payloadReadings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ModeChangeCommand:
    """
    @brief Telecommand: request a transition to a mode.
    """
    targetMode: OperationalMode


@dataclass(frozen=True)
class DataRequestCommand:
    """
    @brief Telecommand: request current readings at any time.
    @param requestedChannels Channel or payload names. Empty means all of them.
    """
    requestedChannels: tuple[str, ...] = ()


@dataclass(frozen=True)
class RerouteCommand:
    """
    @brief Telecommand: replace the current route.
    """
    waypoints: tuple[RouteWaypoint, ...]


Telecommand = Union[ModeChangeCommand, DataRequestCommand, RerouteCommand]


@dataclass
class CommandResponse:
    """
    @brief Acknowledgement or rejection of one telecommand.
    """
    command: Telecommand
    isAccepted: bool
    reason: str


@dataclass
class SampleRecord:
    """
    @brief One collected sample.
    """
    timeSeconds: float
    position: GeographicPosition
    readings: dict[str, float]


@dataclass
class TelemetryFrame:
    """
    @brief One telemetry report (OPS-11).
    @param isReduced True for the minimal safe-mode frame (no payload readings, no samples).
    @param isOnDemand True if the frame answers a data request telecommand.
    """
    timeSeconds: float
    mode: OperationalMode
    position: GeographicPosition
    batteryStateOfCharge: float
    chargingPowerWatts: float
    channelStatuses: dict[str, str]
    channelValues: dict[str, float]
    payloadReadings: dict[str, float]
    samples: list[SampleRecord]
    isReduced: bool = False
    isOnDemand: bool = False


@dataclass
class ModeTransitionRecord:
    """
    @brief Log entry of one accepted mode transition.
    """
    timeSeconds: float
    fromMode: OperationalMode
    toMode: OperationalMode
    source: TransitionSource
    reason: str


@dataclass
class VehicleOutputs:
    """
    @brief Everything the onboard software returns in one tick.
    @param isolatedSubsystems Faulted subsystems that are switched off in safe mode (OPS-10).
    @param crossTrackErrorMeters Signed distance from the active leg, if a route is followed.
    """
    mode: OperationalMode
    propulsionEnabled: bool = False
    desiredHeadingDegrees: float = 0.0
    desiredSpeedMetersPerSecond: float = 0.0
    isolatedSubsystems: frozenset[str] = frozenset()
    crossTrackErrorMeters: float | None = None
    telemetryFrames: list[TelemetryFrame] = field(default_factory=list)
    commandResponses: list[CommandResponse] = field(default_factory=list)
    modeTransitions: list[ModeTransitionRecord] = field(default_factory=list)
