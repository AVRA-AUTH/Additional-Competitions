"""
@file driftAssessor.py
@brief Drift assessment with propulsion off (OPS-8).
"""
from __future__ import annotations

from onboard.onboardConfiguration import OnboardConfiguration
from onboard.onboardTypes import VehicleInputs


class DriftAssessor:
    """
    @brief Assesses whether the vessel can safely drift without propulsion.
    @details calculateDrift() mimics a function that would combine several sensors. For now it
             only returns the value supplied by the simulator.
    @param configuration Onboard configuration.
    """

    def __init__(self, configuration: OnboardConfiguration):
        self.configuration = configuration

    def calculateDrift(self, inputs: VehicleInputs) -> float:
        """
        @brief Drift speed in meters per second without propulsion.
        """
        return inputs.simulatedDriftMetersPerSecond

    def isDriftAcceptable(self, driftMetersPerSecond: float) -> bool:
        """
        @brief True if the drift is at or below the threshold (TBC).
        """
        return driftMetersPerSecond <= self.configuration.driftThresholdMetersPerSecond
