from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import numpy as np
import xarray as xr

from avra.integration.coast import CoastGuard
from avra.integration.mission import create_plan, run_mission, zones_to_planner_inputs
from avra.integration.planner_adapter import (GeographicPosition, GeoPoint, RouteStatus, SubArea, planRoute)
from avra.integration.sensor import CsvStandInSensor, build_standin_dataset
from avra.integration.validator import CrossValidator
from onboard.onboardTypes import SampleRecord


def fixture_dataset(depth: bool = True) -> xr.Dataset:
    times = np.array(["2026-10-27T00:00:00", "2026-10-28T00:00:00"], dtype="datetime64[ns]")
    latitudes = np.linspace(40.25, 40.35, 11)
    longitudes = np.linspace(22.95, 23.05, 11)
    values = np.full((len(times), 1, len(latitudes), len(longitudes)), 0.5, dtype=float)
    if depth:
        return xr.Dataset(
            {"chl": (("time", "depth", "latitude", "longitude"), values)},
            coords={"time": times, "depth": [1.018], "latitude": latitudes, "longitude": longitudes},
        )
    return xr.Dataset(
        {"chl": (("time", "latitude", "longitude"), values[:, 0])},
        coords={"time": times, "latitude": latitudes, "longitude": longitudes},
    )


def request_payload() -> dict:
    return {
        "zones": [
            {"bbox": [22.98, 40.28, 23.04, 40.34], "role": "background", "weight": 1},
            {"bbox": [22.999, 40.299, 23.003, 40.301], "role": "priority", "weight": 2},
        ],
        "start": {"lat": 40.29, "lon": 22.99},
        "budget_meters": 10000,
        "buffer_meters": 30,
        "sample_spacing_meters": 60,
        "cruise_speed_mps": 1.0,
        "enforce_coast_rule": False,
        "divergence_threshold_mg_m3": 0.15,
        "max_simulation_steps": 1200,
        "simulation_step_seconds": 10,
    }


class ZonesAdapterTests(unittest.TestCase):
    def test_weighted_ui_zones_become_four_corner_subareas(self):
        zones = [
            {"bbox": [22.98, 40.28, 23.04, 40.34], "role": "background", "weight": 1},
            {"bbox": [22.999, 40.299, 23.003, 40.301], "role": "priority", "weight": 1},
            {"bbox": [22.999, 40.299, 23.003, 40.301], "role": "priority", "weight": 2},
        ]
        area, subareas = zones_to_planner_inputs(zones)
        self.assertEqual(len(area), 4)
        self.assertEqual(len(subareas), 1)
        self.assertEqual(len(subareas[0].corners), 4)
        self.assertEqual(subareas[0].priority, 3.0)

    def test_target_outside_background_is_rejected(self):
        zones = [
            {"bbox": [22.98, 40.28, 23.04, 40.34], "role": "background"},
            {"bbox": [23.05, 40.30, 23.06, 40.31], "role": "priority"},
        ]
        with self.assertRaisesRegex(ValueError, "outside the background"):
            zones_to_planner_inputs(zones)

    def test_start_coast_rejection_explains_why(self):
        payload = request_payload()
        payload["enforce_coast_rule"] = True
        all_land = lambda latitude, longitude: np.ones_like(np.asarray(latitude), dtype=bool)
        guard = CoastGuard(land_fn=all_land)
        plan, _area, _subareas, metadata = create_plan(payload, coast_guard=guard)
        self.assertEqual(metadata["coast_diagnostics"]["start"]["reason_code"],
                         "land_mask_classifies_point_as_land")
        self.assertTrue(any("start rejected by the land/coast rule" in warning
                            and "classifies" in warning for warning in plan.warnings), plan.warnings)

    def test_planner_rejects_sampling_ring_when_segment_guard_denies_it(self):
        area = [GeoPoint(40.28, 22.98), GeoPoint(40.28, 23.04),
                GeoPoint(40.34, 23.04), GeoPoint(40.34, 22.98)]
        target = SubArea("target", [GeoPoint(40.299, 22.999), GeoPoint(40.299, 23.003),
                                     GeoPoint(40.301, 23.003), GeoPoint(40.301, 22.999)], priority=2)
        plan = planRoute(area, [target], GeoPoint(40.29, 22.99),
                         isAllowed=lambda point: True,
                         isLegAllowed=lambda start, end: False)
        self.assertEqual(plan.status, RouteStatus.INFEASIBLE)
        self.assertEqual(plan.skippedReasons.get("target"), "samplingLegViolatesConstraint")


class CoastGuardTests(unittest.TestCase):
    def test_enforces_sea_and_within_three_kilometres_of_land(self):
        def land_mask(latitude, longitude):
            return np.asarray(latitude) < 0.0

        guard = CoastGuard(land_fn=land_mask, max_distance_km=3.0,
                           coast_probe_spacing_m=250, leg_sample_spacing_m=250)
        self.assertTrue(guard.is_allowed(GeoPoint(0.01, 0.0)))
        self.assertFalse(guard.is_allowed(GeoPoint(0.05, 0.0)))
        self.assertFalse(guard.is_allowed(GeoPoint(-0.001, 0.0)))
        too_far = guard.check_point(GeoPoint(0.05, 0.0))
        self.assertEqual(too_far["reason_code"], "no_land_detected_within_coast_limit")
        on_land = guard.check_point(GeoPoint(-0.001, 0.0))
        self.assertEqual(on_land["reason_code"], "land_mask_classifies_point_as_land")
        self.assertIn("land mask classifies", guard.explain(GeoPoint(-0.001, 0.0)))

    def test_leg_checks_intermediate_points_not_only_endpoints(self):
        # A compact synthetic island between two valid coastal sea endpoints.
        island_mask = lambda latitude, longitude: (np.abs(np.asarray(longitude)) < 0.001) & (np.abs(np.asarray(latitude)) < 0.002)
        guard = CoastGuard(land_fn=island_mask, max_distance_km=3.0,
                           coast_probe_spacing_m=250, leg_sample_spacing_m=100)
        self.assertTrue(guard.is_allowed(GeoPoint(-0.02, 0.0)))
        self.assertTrue(guard.is_allowed(GeoPoint(0.02, 0.0)))
        # The route crosses the island itself, not merely a parallel coastal band.
        self.assertFalse(guard.segment_allowed(GeoPoint(-0.02, 0.0), GeoPoint(0.02, 0.0)))


class ValidatorTests(unittest.TestCase):
    def test_uses_containing_cell_and_reports_bias_and_divergence(self):
        dataset = fixture_dataset(depth=True)
        validator = CrossValidator(dataset, variable="chl", threshold_mg_m3=0.15)
        sample = SampleRecord(
            timeSeconds=120.0,
            position=GeographicPosition(40.30, 23.00),
            readings={"chlorophyll": 0.82},
        )
        record = validator.add_sample(sample, datetime(2026, 10, 27, tzinfo=timezone.utc))
        self.assertEqual(record["copernicus_status"], "ok")
        self.assertAlmostEqual(record["copernicus_chlorophyll_mg_m3"], 0.5)
        self.assertAlmostEqual(record["sensor_minus_copernicus_mg_m3"], 0.32)
        self.assertTrue(record["flagged"])
        summary = validator.summary()
        self.assertEqual(summary["cells_compared"], 1)
        self.assertEqual(summary["cells_flagged"], 1)
        self.assertAlmostEqual(summary["global_bias_sensor_minus_copernicus_mg_m3"], 0.32)

    def test_depth_dimension_is_reduced_to_surface(self):
        dataset = fixture_dataset(depth=True)
        validator = CrossValidator(dataset, variable="chl")
        self.assertEqual(validator.field_index.field(0).dims, ("latitude", "longitude"))


class SensorStandInTests(unittest.TestCase):
    def test_writes_standin_csv_and_can_be_interpolated(self):
        dataset = fixture_dataset()
        _area, subareas = zones_to_planner_inputs(request_payload()["zones"])
        when = datetime(2026, 10, 27, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "standin.csv"
            stations = build_standin_dataset(path, dataset, subareas, "chl", when, grid_size=3)
            self.assertEqual(len(stations), 9)
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 9)
            self.assertTrue(all(row["source"] == "SYNTHETIC_STANDIN_NOT_MEASURED" for row in rows))
            provider = CsvStandInSensor.from_csv(path)
            self.assertGreater(provider.value_at(40.30, 23.00), 0)


class EndToEndMissionTests(unittest.TestCase):
    def test_run_writes_all_core_artifacts(self):
        dataset = fixture_dataset(depth=True)
        with tempfile.TemporaryDirectory() as directory:
            result = run_mission(request_payload(), output_root=directory, dataset=dataset)
            run_dir = Path(result["output_dir"])
            self.assertTrue((run_dir / "initial_plan.json").exists())
            self.assertTrue((run_dir / "crosscheck_samples.csv").exists())
            self.assertTrue((run_dir / "per_cell_validation.csv").exists())
            self.assertTrue((run_dir / "telemetry.csv").exists())
            self.assertTrue((run_dir / "replan_events.json").exists())
            self.assertTrue((run_dir / "summary.json").exists())
            self.assertGreater(result.get("sample_count", 0), 0, result)
            self.assertEqual(result["status"], "complete", result)

    def test_divergence_checkpoint_replans_only_remaining_targets(self):
        dataset = fixture_dataset(depth=True)
        payload = request_payload()
        payload["sample_spacing_meters"] = 100
        payload["zones"].append({
            "bbox": [23.019, 40.319, 23.023, 40.321], "role": "priority", "weight": 1,
        })
        with tempfile.TemporaryDirectory() as directory:
            result = run_mission(payload, output_root=directory, dataset=dataset)
            self.assertEqual(result["status"], "complete", result)
            self.assertEqual(len(result["replans"]), 1, result["replans"])
            self.assertEqual(result["replans"][0]["reason"], "divergence_flagged")
            self.assertEqual(result["replans"][0]["remaining_subareas"], ["zone-3"])
            self.assertEqual([entry["reason"] for entry in result["plan_history"]],
                             ["initial", "divergence_flagged"])


if __name__ == "__main__":
    unittest.main()
