"""
@file onboardGeometry.py
@brief Small geometry helpers (local flat frame, accurate at kilometre scale).
"""
from __future__ import annotations

import math

from onboard.onboardTypes import GeographicPosition

earthRadiusMeters = 6371000.0


def toPlanarMeters(origin: GeographicPosition, position: GeographicPosition) -> tuple[float, float]:
    """
    @brief Converts a position to east and north meters relative to an origin.
    """
    metersPerDegreeLatitude = math.radians(1.0) * earthRadiusMeters
    metersPerDegreeLongitude = metersPerDegreeLatitude * math.cos(math.radians(origin.latitude))
    return ((position.longitude - origin.longitude) * metersPerDegreeLongitude,
            (position.latitude - origin.latitude) * metersPerDegreeLatitude)


def fromPlanarMeters(origin: GeographicPosition, east: float, north: float) -> GeographicPosition:
    """
    @brief Converts east and north meters relative to an origin back to a position.
    """
    metersPerDegreeLatitude = math.radians(1.0) * earthRadiusMeters
    metersPerDegreeLongitude = metersPerDegreeLatitude * math.cos(math.radians(origin.latitude))
    return GeographicPosition(origin.latitude + north / metersPerDegreeLatitude,
                              origin.longitude + east / metersPerDegreeLongitude)


def distanceBetweenPositions(first: GeographicPosition, second: GeographicPosition) -> float:
    east, north = toPlanarMeters(first, second)
    return math.hypot(east, north)


def bearingDegrees(east: float, north: float) -> float:
    """
    @brief Compass bearing of a vector, 0 degrees is north and angles grow clockwise.
    """
    return math.degrees(math.atan2(east, north)) % 360.0
