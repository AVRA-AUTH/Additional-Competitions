"""Import the existing flat-file planner and onboard simulator from one stable place."""
from __future__ import annotations

import sys
from pathlib import Path

from avra import config

PLANNER_DIR = config.ROOT / "onboardsoftware_and_routeplanner"
if str(PLANNER_DIR) not in sys.path:
    sys.path.insert(0, str(PLANNER_DIR))

from routePlanner import (  # noqa: E402
    Budget,
    GeoPoint,
    RoutePlan,
    RouteStatus,
    SamplingSpec,
    SubArea,
    Waypoint,
    planRoute,
    sampleSchedule,
)
from onboard.onboardConfiguration import OnboardConfiguration  # noqa: E402
from onboard.onboardSoftware import OnboardSoftware  # noqa: E402
from onboard.onboardTypes import (  # noqa: E402
    GeographicPosition,
    ModeChangeCommand,
    OperationalMode,
    RerouteCommand,
)
from simulatorExample import SimpleVesselSimulator, convertPlanToRoute  # noqa: E402

__all__ = [
    "Budget", "GeoPoint", "RoutePlan", "RouteStatus", "SamplingSpec", "SubArea", "Waypoint",
    "planRoute", "sampleSchedule", "OnboardConfiguration", "OnboardSoftware", "GeographicPosition",
    "ModeChangeCommand", "OperationalMode", "RerouteCommand", "SimpleVesselSimulator",
    "convertPlanToRoute",
]
