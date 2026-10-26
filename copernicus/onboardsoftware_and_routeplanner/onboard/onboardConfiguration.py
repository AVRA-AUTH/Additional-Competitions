"""
@file onboardConfiguration.py
@brief All tunable numbers of the onboard software in one place.
"""
from __future__ import annotations

from dataclasses import dataclass

knotToMetersPerSecond = 0.514444


@dataclass
class OnboardConfiguration:
    """
    @brief Thresholds and parameters. Values marked TBC are placeholders from the requirements.
    @param standbyEntryBatteryFraction Battery below this (and drift acceptable) enters standby (OPS-8, TBC).
    @param safeEntryBatteryFraction Battery below this enters safe mode (OPS-9, TBC).
    @param autonomousNominalReturnBatteryFraction Battery needed for the autonomous standby to nominal return.
    @param safeExitBatteryFraction Battery needed to leave safe mode after a low-battery safe entry.
    @param driftThresholdMetersPerSecond Largest drift assessed as safe (OPS-8, TBC).
    @param telemetryPeriodSeconds Time between periodic telemetry frames (OPS-13, TBC).
    @param waypointAcceptanceRadiusMeters Distance at which a waypoint counts as reached.
    @param lookaheadDistanceMeters Guidance lookahead along the active leg.
    @param sampleSpacingMeters Distance between samples on a sampling leg.
    @param batteryCapacityWattHours Total battery energy used for the energy assessment.
    @param averagePowerWatts Average power at cruise speed used for the energy assessment.
    """
    standbyEntryBatteryFraction: float = 0.40
    safeEntryBatteryFraction: float = 0.20
    autonomousNominalReturnBatteryFraction: float = 0.70
    safeExitBatteryFraction: float = 0.70
    driftThresholdMetersPerSecond: float = 0.5
    telemetryPeriodSeconds: float = 600.0
    cruiseSpeedMetersPerSecond: float = 2.0 * knotToMetersPerSecond
    waypointAcceptanceRadiusMeters: float = 5.0
    lookaheadDistanceMeters: float = 20.0
    sampleSpacingMeters: float = 5.0
    batteryCapacityWattHours: float = 2178.0
    averagePowerWatts: float = 165.0
