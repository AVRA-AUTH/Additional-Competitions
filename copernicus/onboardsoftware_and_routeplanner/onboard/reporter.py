"""
@file reporter.py
@brief Builds telemetry frames (OPS-11, OPS-13, MSN-12).
"""
from __future__ import annotations

from typing import Optional

from onboard.healthMonitor import HealthReport
from onboard.onboardConfiguration import OnboardConfiguration
from onboard.onboardTypes import OperationalMode, TelemetryFrame, VehicleInputs
from onboard.sampleManager import SampleManager


class Reporter:
    """
    @brief Produces the periodic frame and on-demand frames.
    @details Frame content by mode:
             - Nominal: position, battery, health, payload readings and the unsent samples.
             - Standby: the same without payload readings (samples are still reported, so the
               end of a route does not hold data back).
             - Safe: reduced frame with position, battery, health and mode only (MSN-12).
    @param configuration Onboard configuration.
    """

    def __init__(self, configuration: OnboardConfiguration):
        self.configuration = configuration
        self.nextTelemetryTimeSeconds: Optional[float] = None

    def periodicFrame(self, inputs: VehicleInputs, mode: OperationalMode,
                      healthReport: HealthReport,
                      sampleManager: SampleManager) -> Optional[TelemetryFrame]:
        """
        @brief Returns a frame if the telemetry period has elapsed, otherwise None.
        @details The first call always produces a frame.
        """
        if (self.nextTelemetryTimeSeconds is not None
                and inputs.timeSeconds < self.nextTelemetryTimeSeconds):
            return None
        self.nextTelemetryTimeSeconds = inputs.timeSeconds + self.configuration.telemetryPeriodSeconds
        isReduced = mode == OperationalMode.SAFE
        samples = [] if isReduced else sampleManager.takeUnsentSamples()
        payload = dict(inputs.payloadReadings) if mode == OperationalMode.NOMINAL else {}
        return self.buildFrame(inputs, mode, healthReport, payload, samples, isReduced, False)

    def onDemandFrame(self, inputs: VehicleInputs, mode: OperationalMode, healthReport: HealthReport,
                      requestedChannels: tuple[str, ...]) -> TelemetryFrame:
        """
        @brief Builds the answer to a data request telecommand. Samples are not consumed.
        @param requestedChannels Channel or payload names, empty for all.
        """
        wanted = set(requestedChannels)
        selectedValues = {name: value for name, value in healthReport.channelValues.items()
                          if not wanted or name in wanted}
        selectedPayload = {name: value for name, value in inputs.payloadReadings.items()
                           if not wanted or name in wanted}
        frame = self.buildFrame(inputs, mode, healthReport, selectedPayload, [], False, True)
        frame.channelValues = selectedValues
        frame.channelStatuses = {name: status for name, status in frame.channelStatuses.items()
                                 if name in selectedValues}
        return frame

    @staticmethod
    def buildFrame(inputs: VehicleInputs, mode: OperationalMode, healthReport: HealthReport,
                   payloadReadings: dict[str, float], samples: list, isReduced: bool,
                   isOnDemand: bool) -> TelemetryFrame:
        """
        @brief Assembles a frame from the current state.
        """
        return TelemetryFrame(
            timeSeconds=inputs.timeSeconds, mode=mode, position=inputs.position,
            batteryStateOfCharge=inputs.batteryStateOfCharge,
            chargingPowerWatts=inputs.chargingPowerWatts,
            channelStatuses={name: status.value for name, status in healthReport.channelStatuses.items()},
            channelValues=dict(healthReport.channelValues), payloadReadings=payloadReadings,
            samples=samples, isReduced=isReduced, isOnDemand=isOnDemand)
