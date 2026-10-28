"""Conservative land/coast checks using the same global-land-mask dependency as the UI.

The current planner's optional point hook checks endpoints and ring corners only. This
adapter also supplies a segment hook, sampling every few hundred metres so straight
transit and sampling legs are not allowed to silently cross land or leave the coastal band.
The underlying land mask is coarse and is not a substitute for nautical charts.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np

from .planner_adapter import GeoPoint


def _distance_meters(first: GeoPoint, second: GeoPoint) -> float:
    mean_latitude = math.radians((first.latitude + second.latitude) / 2.0)
    east = math.radians(second.longitude - first.longitude) * 6371000.0 * math.cos(mean_latitude)
    north = math.radians(second.latitude - first.latitude) * 6371000.0
    return math.hypot(east, north)


class CoastGuard:
    """Point and segment validator.

    ``land_fn`` must accept scalar or NumPy-array latitude/longitude inputs and return a
    land boolean (or an array of booleans). The default is the global_land_mask package.
    """

    def __init__(self, land_fn: Optional[Callable] = None, max_distance_km: float = 3.0,
                 coast_probe_spacing_m: float = 250.0, leg_sample_spacing_m: float = 250.0,
                 bearing_step_degrees: int = 5):
        if max_distance_km <= 0:
            raise ValueError("max_distance_km must be positive")
        if coast_probe_spacing_m <= 0 or leg_sample_spacing_m <= 0:
            raise ValueError("sampling spacings must be positive")
        if land_fn is None:
            try:
                from global_land_mask import globe
            except ImportError as error:
                raise RuntimeError(
                    "Coast constraint requires global-land-mask. Install the project's requirements; "
                    "the mission will not plan without the MSN-13 check."
                ) from error
            land_fn = globe.is_land
        self.land_fn = land_fn
        self.max_distance_m = max_distance_km * 1000.0
        self.coast_probe_spacing_m = coast_probe_spacing_m
        self.leg_sample_spacing_m = leg_sample_spacing_m
        self.bearings = np.arange(0, 360, bearing_step_degrees, dtype=float)
        self.radii = np.arange(coast_probe_spacing_m, self.max_distance_m + 1e-6,
                               coast_probe_spacing_m, dtype=float)
        # Cache the decision and its explanation together. The old bool-only cache made
        # "allowed-region" failures impossible to diagnose from the API/UI.
        self._check_cache: dict[tuple[int, int], dict[str, object]] = {}

    def _land_many(self, latitudes, longitudes) -> np.ndarray:
        latitudes = np.asarray(latitudes, dtype=float)
        longitudes = np.asarray(longitudes, dtype=float)
        try:
            result = self.land_fn(latitudes, longitudes)
            return np.broadcast_to(np.asarray(result, dtype=bool), latitudes.shape)
        except Exception:
            # Some user-supplied test masks only support scalar arguments.
            return np.asarray([bool(self.land_fn(float(lat), float(lon)))
                               for lat, lon in zip(latitudes.flat, longitudes.flat)],
                              dtype=bool).reshape(latitudes.shape)

    def check_point(self, point: GeoPoint) -> dict[str, object]:
        """Return the MSN-13 decision with a reason suitable for logs and API responses.

        The allowed-region rule means: classified as sea, with land detected by the
        configured land mask within ``max_distance_m``. This is deliberately stricter
        than merely being inside the drawn area-of-interest rectangle.
        """
        key = (round(point.latitude * 100000), round(point.longitude * 100000))
        if key in self._check_cache:
            return dict(self._check_cache[key])

        if bool(self._land_many([point.latitude], [point.longitude])[0]):
            result: dict[str, object] = {
                "allowed": False,
                "reason_code": "land_mask_classifies_point_as_land",
                "detail": (
                    f"The land mask classifies ({point.latitude:.6f}, {point.longitude:.6f}) "
                    "as land. The point may be on land according to the mask, or too close "
                    "to the mapped shoreline for this mask's resolution."
                ),
                "max_coast_distance_km": self.max_distance_m / 1000.0,
                "land_probe_count": 0,
                "nearest_land_probe_km": None,
            }
            self._check_cache[key] = result
            return dict(result)

        radius_grid, bearing_grid = np.meshgrid(self.radii, self.bearings, indexing="ij")
        north_m = radius_grid * np.cos(np.radians(bearing_grid))
        east_m = radius_grid * np.sin(np.radians(bearing_grid))
        latitudes = point.latitude + north_m / 111195.0
        cos_lat = max(0.1, math.cos(math.radians(point.latitude)))
        longitudes = point.longitude + east_m / (111195.0 * cos_lat)
        land_samples = self._land_many(latitudes, longitudes)
        found_land_nearby = bool(land_samples.any())
        nearest_probe_km = None
        if found_land_nearby:
            # Each row corresponds to a probe radius; report the nearest ring containing
            # at least one land sample (an estimate, not a surveyed coast distance).
            nearest_row = int(np.flatnonzero(land_samples.any(axis=1))[0])
            nearest_probe_km = float(self.radii[nearest_row] / 1000.0)
            result = {
                "allowed": True,
                "reason_code": "sea_with_land_detected_within_limit",
                "detail": (
                    f"The land mask classifies the point as sea and detects land on a probe "
                    f"ring about {nearest_probe_km:.2f} km away. This is a coarse mask-based "
                    "estimate, not a surveyed coast distance."
                ),
                "max_coast_distance_km": self.max_distance_m / 1000.0,
                "land_probe_count": int(land_samples.size),
                "nearest_land_probe_km": nearest_probe_km,
            }
        else:
            result = {
                "allowed": False,
                "reason_code": "no_land_detected_within_coast_limit",
                "detail": (
                    f"The land mask classifies the point as sea, but none of {land_samples.size} "
                    f"coast probes detected land within {self.max_distance_m / 1000.0:.1f} km. "
                    "The point may genuinely be outside the permitted coastal band, or the "
                    "coarse land mask/probe grid may be missing a narrow shoreline."
                ),
                "max_coast_distance_km": self.max_distance_m / 1000.0,
                "land_probe_count": int(land_samples.size),
                "nearest_land_probe_km": None,
            }
        self._check_cache[key] = result
        return dict(result)

    def is_allowed(self, point: GeoPoint) -> bool:
        """True iff the point is sea and sampled land is detected within the coast band."""
        return bool(self.check_point(point)["allowed"])

    def explain(self, point: GeoPoint) -> str:
        """Human-readable reason for the current allowed-region decision."""
        return str(self.check_point(point)["detail"])

    def segment_allowed(self, start: GeoPoint, end: GeoPoint) -> bool:
        """Check the whole segment, not just its endpoints."""
        length = _distance_meters(start, end)
        segments = max(1, int(math.ceil(length / self.leg_sample_spacing_m)))
        for index in range(segments + 1):
            fraction = index / segments
            point = GeoPoint(
                start.latitude + fraction * (end.latitude - start.latitude),
                start.longitude + fraction * (end.longitude - start.longitude),
            )
            if not self.is_allowed(point):
                return False
        return True


class _PermissiveCoastGuard:
    """Duck-typed no-op guard for unit tests only; it has no real geographic safety checks."""

    @staticmethod
    def is_allowed(point: GeoPoint) -> bool:
        return True

    @staticmethod
    def segment_allowed(start: GeoPoint, end: GeoPoint) -> bool:
        return True


def permissive_coast_guard() -> _PermissiveCoastGuard:
    """Testing helper which bypasses all land/coast checks; never use for real missions."""
    return _PermissiveCoastGuard()
