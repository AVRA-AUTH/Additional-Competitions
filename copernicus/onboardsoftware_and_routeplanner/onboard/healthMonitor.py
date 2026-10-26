"""
@file healthMonitor.py
@brief Monitored channels, limits, fault table and self-test results (FUN-1, OPS-6, OPS-7, OPS-10).
@details The fault table is data, so the failure sets of OPS-7 can be filled in after the FMEA
         without changing any logic. All limits below are placeholders (TBC).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from onboard.onboardTypes import ChannelStatus, FaultResponse, SelfTest, VehicleInputs


@dataclass(frozen=True)
class ChannelDefinition:
    """
    @brief One monitored value with its limits and the response to a fault.
    @param subsystem Subsystem that is isolated when this channel faults.
    """
    name: str
    subsystem: str
    warningMinimum: Optional[float] = None
    warningMaximum: Optional[float] = None
    faultMinimum: Optional[float] = None
    faultMaximum: Optional[float] = None
    faultResponse: FaultResponse = FaultResponse.REPORT_ONLY


@dataclass
class HealthReport:
    """
    @brief Result of one health evaluation.
    @param isolatedSubsystems Subsystems to switch off in safe mode.
    @param safeTriggerSelfTests Self-tests that fail because a channel with an enter-safe-mode
           response faulted. A mode only enters safe mode for these if it depends on them.
    @param selfTestResults True for every self-test whose channels have no fault.
    """
    channelValues: dict[str, float]
    channelStatuses: dict[str, ChannelStatus]
    faultedSubsystems: frozenset[str]
    isolatedSubsystems: frozenset[str]
    safeTriggerSelfTests: frozenset[SelfTest]
    selfTestResults: dict[SelfTest, bool] = field(default_factory=dict)


def defaultChannelDefinitions() -> list[ChannelDefinition]:
    """
    @brief Example channels: battery state of charge, current and temperature, GPS fix, thruster
           status and the communication link. Limits are TBC placeholders.
    @details Battery state of charge is not an FDIR fault: the low-battery rules (OPS-8, OPS-9)
             belong to the mode manager.
    """
    isolate = FaultResponse.ENTER_SAFE_MODE_AND_ISOLATE
    return [
        ChannelDefinition("batteryStateOfCharge", "battery", warningMinimum=0.30),
        ChannelDefinition("batteryCurrent", "battery", warningMaximum=40.0, faultMaximum=60.0,
                          faultResponse=isolate),
        ChannelDefinition("batteryTemperature", "battery", warningMaximum=50.0, faultMaximum=60.0,
                          faultResponse=isolate),
        ChannelDefinition("gpsFix", "navigation", faultMinimum=0.5, faultResponse=isolate),
        ChannelDefinition("thrusterStatus", "propulsion", faultMinimum=0.5, faultResponse=isolate),
        ChannelDefinition("communicationLink", "communication", faultMinimum=0.5),
    ]


defaultSelfTestChannels: dict[SelfTest, tuple[str, ...]] = {
    SelfTest.POWER: ("batteryCurrent", "batteryTemperature"),
    SelfTest.NAVIGATION: ("gpsFix",),
    SelfTest.PROPULSION: ("thrusterStatus",),
    SelfTest.COMMUNICATION: ("communicationLink",),
}


class HealthMonitor:
    """
    @brief Evaluates the monitored channels against their limits.
    @param channelDefinitions Channel table, defaults to defaultChannelDefinitions().
    @param selfTestChannels Which channels each self-test depends on.
    """

    def __init__(self, channelDefinitions: Optional[list[ChannelDefinition]] = None,
                 selfTestChannels: Optional[dict[SelfTest, tuple[str, ...]]] = None):
        self.channelDefinitions = channelDefinitions or defaultChannelDefinitions()
        self.selfTestChannels = selfTestChannels or defaultSelfTestChannels

    @staticmethod
    def channelValuesFromInputs(inputs: VehicleInputs) -> dict[str, float]:
        """
        @brief Maps the vehicle inputs to the numeric channel values (booleans become 1.0 or 0.0).
        """
        return {
            "batteryStateOfCharge": inputs.batteryStateOfCharge,
            "batteryCurrent": inputs.batteryCurrentAmperes,
            "batteryTemperature": inputs.batteryTemperatureCelsius,
            "gpsFix": 1.0 if inputs.hasGpsFix else 0.0,
            "thrusterStatus": 1.0 if inputs.thrusterIsOperational else 0.0,
            "communicationLink": 1.0 if inputs.communicationLinkIsUp else 0.0,
        }

    @staticmethod
    def statusOf(definition: ChannelDefinition, value: float) -> ChannelStatus:
        """
        @brief Compares one value with the limits of its channel.
        """
        if definition.faultMinimum is not None and value < definition.faultMinimum:
            return ChannelStatus.FAULT
        if definition.faultMaximum is not None and value > definition.faultMaximum:
            return ChannelStatus.FAULT
        if definition.warningMinimum is not None and value < definition.warningMinimum:
            return ChannelStatus.WARNING
        if definition.warningMaximum is not None and value > definition.warningMaximum:
            return ChannelStatus.WARNING
        return ChannelStatus.NORMAL

    def evaluate(self, inputs: VehicleInputs) -> HealthReport:
        """
        @brief Evaluates every channel, the fault table and the self-tests.
        @return The health report for this tick.
        """
        values = self.channelValuesFromInputs(inputs)
        statuses: dict[str, ChannelStatus] = {}
        faultedSubsystems: set[str] = set()
        isolatedSubsystems: set[str] = set()
        safeTriggerChannels: set[str] = set()
        for definition in self.channelDefinitions:
            status = self.statusOf(definition, values[definition.name])
            statuses[definition.name] = status
            if status == ChannelStatus.FAULT:
                faultedSubsystems.add(definition.subsystem)
                if definition.faultResponse == FaultResponse.ENTER_SAFE_MODE_AND_ISOLATE:
                    isolatedSubsystems.add(definition.subsystem)
                    safeTriggerChannels.add(definition.name)
        selfTestResults = {
            selfTest: all(statuses[name] != ChannelStatus.FAULT for name in channelNames)
            for selfTest, channelNames in self.selfTestChannels.items()}
        safeTriggerSelfTests = frozenset(
            selfTest for selfTest, channelNames in self.selfTestChannels.items()
            if safeTriggerChannels.intersection(channelNames))
        return HealthReport(values, statuses, frozenset(faultedSubsystems),
                            frozenset(isolatedSubsystems), safeTriggerSelfTests, selfTestResults)
