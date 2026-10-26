"""
@file modeProfile.py
@brief Which functions run in each mode and which self-tests each mode depends on.
"""
from __future__ import annotations

from onboard.onboardTypes import FunctionName, OperationalMode, SelfTest

modeFunctions: dict[OperationalMode, frozenset[FunctionName]] = {
    OperationalMode.NOMINAL: frozenset(FunctionName),
    OperationalMode.STANDBY: frozenset({FunctionName.TELEMETRY, FunctionName.TELECOMMAND,
                                        FunctionName.NAVIGATION}),
    OperationalMode.SAFE: frozenset({FunctionName.TELEMETRY, FunctionName.TELECOMMAND,
                                     FunctionName.NAVIGATION}),
}

modeRequiredSelfTests: dict[OperationalMode, frozenset[SelfTest]] = {
    OperationalMode.NOMINAL: frozenset(SelfTest),
    OperationalMode.STANDBY: frozenset({SelfTest.POWER, SelfTest.NAVIGATION, SelfTest.COMMUNICATION}),
    OperationalMode.SAFE: frozenset(),
}


def isFunctionEnabled(mode: OperationalMode, function: FunctionName) -> bool:
    """
    @brief Tests whether a function runs in a mode (OPS-2, OPS-3, OPS-4, OPS-9).
    """
    return function in modeFunctions[mode]
