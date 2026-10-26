"""
@file energyAssessor.py
@brief Energy feasibility check of a route (OPS-14).
"""
from __future__ import annotations

from onboard.onboardConfiguration import OnboardConfiguration

secondsPerHour = 3600.0


class EnergyAssessor:
    """
    @brief Decides whether the remaining energy covers the remaining route.
    @details A route is feasible if it can be finished without the battery dropping below the safe
             mode threshold, so the ASV ends the route in a safe state (it can then wait in standby).
    @param configuration Onboard configuration.
    """

    def __init__(self, configuration: OnboardConfiguration):
        self.configuration = configuration

    def availableEnergyWattHours(self, stateOfCharge: float) -> float:
        """
        @brief Energy that may be used before the safe mode threshold is reached.
        """
        usableFraction = max(0.0, stateOfCharge - self.configuration.safeEntryBatteryFraction)
        return usableFraction * self.configuration.batteryCapacityWattHours

    def requiredEnergyWattHours(self, distanceMeters: float) -> float:
        """
        @brief Energy needed to travel a distance at cruise speed and average power.
        """
        durationSeconds = distanceMeters / self.configuration.cruiseSpeedMetersPerSecond
        return durationSeconds / secondsPerHour * self.configuration.averagePowerWatts

    def canCompleteDistance(self, stateOfCharge: float, distanceMeters: float) -> bool:
        """
        @brief Tests whether the remaining distance can be completed with the available energy.
        """
        return self.requiredEnergyWattHours(distanceMeters) <= self.availableEnergyWattHours(stateOfCharge)
