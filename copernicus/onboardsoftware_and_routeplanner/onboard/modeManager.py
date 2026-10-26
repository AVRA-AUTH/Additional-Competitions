"""
@file modeManager.py
@brief The mode state machine: Nominal, Standby and Safe (OPS-1 to OPS-10, OPS-14).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from onboard.healthMonitor import HealthReport
from onboard.modeProfile import modeRequiredSelfTests
from onboard.onboardConfiguration import OnboardConfiguration
from onboard.onboardTypes import (ModeTransitionRecord, OperationalMode, SelfTest,
                                  TransitionSource)

lowBatteryReason = "lowBattery"
faultReason = "fault"
telecommandReason = "telecommand"


@dataclass
class ModeContext:
    """
    @brief Everything the state machine needs to decide, collected once per decision.
    @param hasPendingRoute True if a route is loaded and not finished.
    @param isRouteFeasible True if the remaining energy covers the remaining route (OPS-14).
    @param isRouteComplete True if a route was loaded and is finished.
    """
    stateOfCharge: float
    healthReport: HealthReport
    isDriftAcceptable: bool
    hasPendingRoute: bool
    isRouteFeasible: bool
    isRouteComplete: bool


@dataclass
class TransitionDecision:
    """
    @brief Outcome of a requested transition.
    """
    isAccepted: bool
    reason: str


class ModeManager:
    """
    @brief Holds the current mode and decides every transition.
    @details Rules (any mode to any mode is possible, OPS-5):
             - Entering safe mode is always accepted.
             - Safe mode is left only by telecommand (also covers OPS-6, extended to every target).
             - Entering a mode needs the self-tests that mode depends on to pass.
             - Leaving safe mode after a low-battery entry needs the battery at the safe exit level.
             - Entering nominal mode with a pending route needs the route to be energy-feasible (OPS-14).
    @param configuration Onboard configuration.
    @param initialMode Mode at power-up.
    """

    def __init__(self, configuration: OnboardConfiguration,
                 initialMode: OperationalMode = OperationalMode.STANDBY):
        self.configuration = configuration
        self.mode = initialMode
        self.safeEntryReasons: set[str] = set()
        self.standbyEnteredForLowBattery = False
        self.transitionLog: list[ModeTransitionRecord] = []
        self.newTransitions: list[ModeTransitionRecord] = []

    @staticmethod
    def failingSelfTests(targetMode: OperationalMode, context: ModeContext) -> list[SelfTest]:
        """
        @brief Self-tests the target mode depends on that currently fail.
        """
        results = context.healthReport.selfTestResults
        return [selfTest for selfTest in modeRequiredSelfTests[targetMode]
                if not results.get(selfTest, False)]

    def evaluateTransition(self, targetMode: OperationalMode, source: TransitionSource,
                           context: ModeContext) -> TransitionDecision:
        """
        @brief Applies the guards to a requested transition without changing the mode.
        """
        if targetMode == self.mode:
            return TransitionDecision(True, f"already in {targetMode.value} mode")
        if self.mode == OperationalMode.SAFE:
            if source == TransitionSource.AUTONOMOUS:
                return TransitionDecision(False, "safe mode can only be left by telecommand")
            if (lowBatteryReason in self.safeEntryReasons
                    and context.stateOfCharge < self.configuration.safeExitBatteryFraction):
                return TransitionDecision(False, "battery below the safe mode exit level")
        if targetMode == OperationalMode.SAFE:
            return TransitionDecision(True, "safe mode is always accepted")
        failing = self.failingSelfTests(targetMode, context)
        if failing:
            names = ", ".join(selfTest.value for selfTest in failing)
            return TransitionDecision(False, f"self-test failing: {names}")
        if (targetMode == OperationalMode.NOMINAL and context.hasPendingRoute
                and not context.isRouteFeasible):
            return TransitionDecision(False, "OPS-14: not enough energy to complete the route")
        return TransitionDecision(True, "accepted")

    def requestTransition(self, targetMode: OperationalMode, source: TransitionSource,
                          context: ModeContext, timeSeconds: float,
                          reason: str) -> TransitionDecision:
        """
        @brief Evaluates a transition and applies it if accepted.
        @param reason Why the transition is requested (recorded in the transition log).
        """
        decision = self.evaluateTransition(targetMode, source, context)
        if decision.isAccepted and targetMode != self.mode:
            self.applyTransition(targetMode, source, timeSeconds, reason)
        return decision

    def applyTransition(self, targetMode: OperationalMode, source: TransitionSource,
                        timeSeconds: float, reason: str) -> None:
        """
        @brief Changes the mode and updates the entry bookkeeping.
        """
        record = ModeTransitionRecord(timeSeconds, self.mode, targetMode, source, reason)
        self.transitionLog.append(record)
        self.newTransitions.append(record)
        self.mode = targetMode
        self.safeEntryReasons = {reason} if targetMode == OperationalMode.SAFE else set()
        self.standbyEnteredForLowBattery = (
            targetMode == OperationalMode.STANDBY and source == TransitionSource.AUTONOMOUS
            and reason == lowBatteryReason)

    def evaluateAutonomousTransition(
            self, context: ModeContext) -> Optional[tuple[OperationalMode, str]]:
        """
        @brief Picks the autonomous transition due now, if any. Safe triggers have priority.
        @details A fault only triggers safe mode if the current mode depends on the failing
                 self-test, so a failed thruster does not push standby back into safe mode.
        @return The target mode and the reason, or None.
        """
        configuration = self.configuration
        if self.mode != OperationalMode.SAFE:
            if context.stateOfCharge < configuration.safeEntryBatteryFraction:
                return OperationalMode.SAFE, lowBatteryReason
            dependedSelfTests = modeRequiredSelfTests[self.mode]
            if dependedSelfTests.intersection(context.healthReport.safeTriggerSelfTests):
                return OperationalMode.SAFE, faultReason
        if self.mode == OperationalMode.NOMINAL:
            if (context.stateOfCharge < configuration.standbyEntryBatteryFraction
                    and context.isDriftAcceptable):
                return OperationalMode.STANDBY, lowBatteryReason
            if context.isRouteComplete:
                return OperationalMode.STANDBY, "routeComplete"
        if (self.mode == OperationalMode.STANDBY and self.standbyEnteredForLowBattery
                and context.hasPendingRoute
                and context.stateOfCharge >= configuration.autonomousNominalReturnBatteryFraction):
            return OperationalMode.NOMINAL, "batteryRecovered"
        return None

    def takeNewTransitions(self) -> list[ModeTransitionRecord]:
        """
        @brief Returns the transitions since the last call and clears them.
        """
        newTransitions, self.newTransitions = self.newTransitions, []
        return newTransitions
