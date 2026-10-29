"""Application service that connects the map's zones to planning, simulation and validation."""
from __future__ import annotations

import csv
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from avra import config
from avra.copernicus.client import fetch
from .coast import CoastGuard
from .planner_adapter import (
    Budget, GeoPoint, OnboardConfiguration, OnboardSoftware, OperationalMode,
    GeographicPosition, ModeChangeCommand, RerouteCommand, RoutePlan, RouteStatus,
    SamplingSpec, SimpleVesselSimulator, SubArea, convertPlanToRoute, planRoute,
)
from .sensor import CsvStandInSensor, SensorStation, build_standin_dataset
from .validator import CopernicusFieldIndex, CrossValidator

DEFAULT_THRESHOLD_MG_M3 = 0.15
DEFAULT_COAST_RULE_KM = 3.0
DEFAULT_BUDGET_METERS = 30000.0
DEFAULT_CRUISE_SPEED_MPS = 2.0 * 0.514444


def _rectangle_corners(bbox: list[float] | tuple[float, float, float, float]) -> list[GeoPoint]:
    if len(bbox) != 4:
        raise ValueError("bbox must be [lon_min, lat_min, lon_max, lat_max]")
    lon_min, lat_min, lon_max, lat_max = [float(value) for value in bbox]
    if not all(math.isfinite(value) for value in (lon_min, lat_min, lon_max, lat_max)):
        raise ValueError("bbox contains a non-finite coordinate")
    if not (-180 <= lon_min < lon_max <= 180 and -90 <= lat_min < lat_max <= 90):
        raise ValueError("bbox must have valid non-zero longitude/latitude extents")
    return [GeoPoint(lat_min, lon_min), GeoPoint(lat_min, lon_max),
            GeoPoint(lat_max, lon_max), GeoPoint(lat_max, lon_min)]


def zones_to_planner_inputs(zones: list[dict[str, Any]]) -> tuple[list[GeoPoint], list[SubArea]]:
    """Convert Dash zone dictionaries into the planner's area + four-corner SubAreas.

    The first/background rectangle defines the allowed planning area. Subsequent rectangles
    are monitoring targets. Exact duplicates are collapsed and their weights summed (the
    natural interpretation of drawing the same rectangle repeatedly to increase priority).
    Nested/overlapping distinct targets receive the sum of weights covering their centre.
    """
    if not zones or len(zones) < 2:
        raise ValueError("Draw one background area and at least one monitoring rectangle.")

    normalized = []
    for index, item in enumerate(zones):
        if not isinstance(item, dict) or "bbox" not in item:
            raise ValueError(f"zone {index + 1} must be an object containing bbox")
        bbox = [float(value) for value in item["bbox"]]
        corners = _rectangle_corners(bbox)
        role = str(item.get("role", "background" if index == 0 else "priority"))
        weight = float(item.get("weight", 1.0))
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"zone {index + 1} weight must be positive")
        normalized.append({"index": index, "bbox": bbox, "corners": corners,
                           "role": role, "weight": weight})

    background = next((zone for zone in normalized if zone["role"] == "background"), normalized[0])
    area_bbox = background["bbox"]
    lon_min, lat_min, lon_max, lat_max = area_bbox
    area = background["corners"]

    targets = []
    for zone in normalized:
        if zone is background or zone["role"] == "background":
            continue
        west, south, east, north = zone["bbox"]
        if west < lon_min - 1e-9 or east > lon_max + 1e-9 or south < lat_min - 1e-9 or north > lat_max + 1e-9:
            raise ValueError(f"monitoring zone {zone['index'] + 1} is outside the background area")
        targets.append(zone)
    if not targets:
        raise ValueError("No monitoring rectangles found. Add one or more rectangles after the background area.")

    grouped: dict[tuple[float, float, float, float], dict[str, Any]] = {}
    for zone in targets:
        key = tuple(round(value, 7) for value in zone["bbox"])
        if key not in grouped:
            grouped[key] = {"bbox": zone["bbox"], "corners": zone["corners"],
                            "weight": zone["weight"], "first_index": zone["index"]}
        else:
            grouped[key]["weight"] += zone["weight"]
            grouped[key]["first_index"] = min(grouped[key]["first_index"], zone["index"])
    groups = list(grouped.values())

    subareas: list[SubArea] = []
    for group in groups:
        west, south, east, north = group["bbox"]
        center_lon, center_lat = (west + east) / 2.0, (south + north) / 2.0
        effective_priority = sum(
            other["weight"] for other in groups
            if other["bbox"][0] - 1e-9 <= center_lon <= other["bbox"][2] + 1e-9
            and other["bbox"][1] - 1e-9 <= center_lat <= other["bbox"][3] + 1e-9
        )
        subareas.append(SubArea(
            identifier=f"zone-{group['first_index'] + 1}",
            corners=group["corners"],
            priority=max(effective_priority, group["weight"]),
        ))
    return area, subareas


def plan_to_dict(plan: RoutePlan) -> dict[str, Any]:
    return {
        "status": plan.status.value,
        "waypoints": [{
            "latitude": waypoint.latitude,
            "longitude": waypoint.longitude,
            "kind": waypoint.kind,
            "subAreaIdentifier": waypoint.subAreaIdentifier,
            "isSamplingLeg": waypoint.isSamplingLeg,
        } for waypoint in plan.waypoints],
        "total_distance_m": plan.totalDistanceMeters,
        "transit_distance_m": plan.transitDistanceMeters,
        "sampling_distance_m": plan.samplingDistanceMeters,
        "estimated_duration_s": plan.estimatedDurationSeconds,
        "estimated_sample_count": plan.estimatedSampleCount,
        "covered_subareas": plan.coveredSubAreaIdentifiers,
        "skipped_reasons": plan.skippedReasons,
        "warnings": plan.warnings,
    }


def _position_from_request(start: dict[str, Any]) -> GeoPoint:
    latitude = start.get("lat", start.get("latitude"))
    longitude = start.get("lon", start.get("lng", start.get("longitude")))
    if latitude is None or longitude is None:
        raise ValueError("start must contain lat/lon")
    latitude, longitude = float(latitude), float(longitude)
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("start position must be finite")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("start position is outside valid geographic ranges")
    return GeoPoint(latitude, longitude)


def _new_run_dir(output_root: str | Path) -> tuple[str, Path]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    path = Path(output_root) / run_id
    path.mkdir(parents=True, exist_ok=False)
    return run_id, path


def _json_dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str, allow_nan=False), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns = list(dict.fromkeys(key for row in rows for key in row.keys()))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: (json.dumps(value) if isinstance(value, (dict, list)) else value)
                             for key, value in row.items()})


def _route_exit_indices(plan: RoutePlan) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, waypoint in enumerate(plan.waypoints):
        if waypoint.kind == "ringExit" and waypoint.subAreaIdentifier:
            result[waypoint.subAreaIdentifier] = index
    return result


def _position_dict(position: GeographicPosition) -> dict[str, float]:
    return {"latitude": float(position.latitude), "longitude": float(position.longitude)}


def create_plan(request_data: dict[str, Any], dataset=None, coast_guard: Optional[CoastGuard] = None) -> tuple[RoutePlan, list[GeoPoint], list[SubArea], dict[str, Any]]:
    """Pure-ish planning service used by both REST and the Dash callback."""
    zones = request_data.get("zones") or []
    area, subareas = zones_to_planner_inputs(zones)
    start = _position_from_request(request_data.get("start") or {})
    budget_m = float(request_data.get("budget_meters", DEFAULT_BUDGET_METERS))
    buffer_m = float(request_data.get("buffer_meters", 50.0))
    spacing_m = float(request_data.get("sample_spacing_meters", 20.0))
    speed_mps = float(request_data.get("cruise_speed_mps", DEFAULT_CRUISE_SPEED_MPS))
    if not all(math.isfinite(value) for value in (budget_m, buffer_m, spacing_m, speed_mps)):
        raise ValueError("budget, buffer, spacing and speed must be finite")
    if budget_m <= 0 or buffer_m < 0 or spacing_m <= 0 or speed_mps <= 0:
        raise ValueError("budget/speed/spacing must be positive and buffer cannot be negative")

    enforce_coast = bool(request_data.get("enforce_coast_rule", True))
    if enforce_coast and coast_guard is None:
        coast_guard = CoastGuard(max_distance_km=float(request_data.get("coast_rule_km", DEFAULT_COAST_RULE_KM)))
    sampling = SamplingSpec(bufferMeters=buffer_m, sampleSpacingMeters=spacing_m)
    plan = planRoute(area, subareas, start,
                     budget=Budget(maxDistanceMeters=budget_m), sampling=sampling,
                     cruiseSpeedMetersPerSecond=speed_mps,
                     isAllowed=coast_guard.is_allowed if coast_guard else None,
                     isLegAllowed=coast_guard.segment_allowed if coast_guard else None)
    coast_diagnostics = {
        "rule_enabled": bool(coast_guard),
        "rule_distance_km": float(request_data.get("coast_rule_km", DEFAULT_COAST_RULE_KM)),
        "start": (coast_guard.check_point(start) if coast_guard and hasattr(coast_guard, "check_point")
                  else {"allowed": bool(coast_guard.is_allowed(start)), "detail": "Custom coast guard result"}
                  if coast_guard else {"allowed": True, "detail": "Coast rule disabled"}),
    }
    # Preserve the planner's status semantics but replace its opaque boolean-hook warning
    # with the actual land/coast reason, which makes failed requests debuggable in the UI.
    for index, warning in enumerate(plan.warnings):
        if warning == "start violates the allowed-region constraint" and coast_guard and hasattr(coast_guard, "explain"):
            plan.warnings[index] = f"start rejected by the land/coast rule: {coast_guard.explain(start)}"
    area_lats = [point.latitude for point in area]
    area_lons = [point.longitude for point in area]
    metadata = {
        "start": {"latitude": start.latitude, "longitude": start.longitude},
        "area_bbox": [min(area_lons), min(area_lats), max(area_lons), max(area_lats)],
        "targets": [{"id": area.identifier, "priority": area.priority,
                     "corners": [{"latitude": point.latitude, "longitude": point.longitude}
                                 for point in area.corners]} for area in subareas],
        "budget_meters": budget_m,
        "buffer_meters": buffer_m,
        "sample_spacing_meters": spacing_m,
        "cruise_speed_mps": speed_mps,
        "coast_rule_enforced": bool(coast_guard),
        "coast_rule_km": float(request_data.get("coast_rule_km", DEFAULT_COAST_RULE_KM)),
        "coast_diagnostics": coast_diagnostics,
    }
    return plan, area, subareas, metadata


def run_mission(request_data: dict[str, Any], output_root: str | Path | None = None,
                dataset=None, coast_guard: Optional[CoastGuard] = None,
                sensor_csv: str | Path | None = None,
                forecast_refresher: Optional[Callable[[], Any]] = None) -> dict[str, Any]:
    """Run fetch -> plan -> simulated ASV -> cross-check -> event-driven replan.

    ``sensor_csv`` is a path to a real/supplied CSV with latitude, longitude and
    chlorophyll_mg_m3 columns. Without it the run builds a documented synthetic stand-in CSV.
    """
    output_root = Path(output_root or (config.ROOT / "runs"))
    run_id, run_dir = _new_run_dir(output_root)
    mission_start = datetime.now(timezone.utc)
    req = dict(request_data)
    if dataset is None:
        dataset = fetch(refresh=bool(req.get("refresh_copernicus", False)))
    variable = str(req.get("variable") or config.VARIABLE)
    if variable not in dataset.data_vars:
        raise ValueError(f"Variable {variable!r} is not present in the Copernicus cache")
    threshold = float(req.get("divergence_threshold_mg_m3", DEFAULT_THRESHOLD_MG_M3))
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("divergence_threshold_mg_m3 must be a finite positive number")
    plan, area, subareas, plan_metadata = create_plan(req, dataset=dataset, coast_guard=coast_guard)
    initial_source_latest = str(dataset.time.values.max())
    _json_dump(run_dir / "mission_request.json", {**req, "mission_start_utc": mission_start.isoformat()})
    _json_dump(run_dir / "planning_inputs.json", plan_metadata)
    _json_dump(run_dir / "initial_plan.json", plan_to_dict(plan))
    _json_dump(run_dir / "copernicus_metadata.json", {
        "dataset_id": config.DATASET_ID, "variable": variable,
        "available_time_start": str(dataset.time.values.min()),
        "available_time_end": initial_source_latest,
        "latitude_min": float(dataset.latitude.values.min()),
        "latitude_max": float(dataset.latitude.values.max()),
        "longitude_min": float(dataset.longitude.values.min()),
        "longitude_max": float(dataset.longitude.values.max()),
        "mission_start_utc": mission_start.isoformat(),
        "note": "Comparison uses the nearest valid daily product timestamp and the containing grid cell; no neighbouring-cell fallback is used.",
    })

    if plan.status == RouteStatus.INFEASIBLE or not plan.waypoints:
        summary = {
            "run_id": run_id, "status": "planning_failed", "output_dir": str(run_dir),
            "message": "No feasible route. Inspect initial_plan.json for coast, geometry or budget reasons.",
            "plan": plan_to_dict(plan), "mission_start_utc": mission_start.isoformat(),
            "completed_subareas": [], "replans": [], "sample_count": 0,
        }
        _json_dump(run_dir / "summary.json", summary)
        return summary

    standin_path = run_dir / "in_situ_standin.csv"
    if sensor_csv:
        sensor = CsvStandInSensor.from_csv(sensor_csv)
        source_label = "user_csv:" + Path(sensor_csv).name
    else:
        build_standin_dataset(standin_path, dataset, subareas, variable, mission_start)
        sensor = CsvStandInSensor.from_csv(standin_path)
        source_label = "synthetic_standin_csv_not_measured"
    validator = CrossValidator(dataset, variable=variable, threshold_mg_m3=threshold,
                               fallback_cells=0, sensor_source=source_label)

    speed_mps = float(req.get("cruise_speed_mps", DEFAULT_CRUISE_SPEED_MPS))
    spacing_m = float(req.get("sample_spacing_meters", 20.0))
    onboard_config = OnboardConfiguration(cruiseSpeedMetersPerSecond=speed_mps,
                                          sampleSpacingMeters=spacing_m)
    software = OnboardSoftware(configuration=onboard_config, initialMode=OperationalMode.STANDBY)
    start_position = plan.waypoints[0]
    simulator = SimpleVesselSimulator(
        GeographicPosition(start_position.latitude, start_position.longitude),
        stateOfCharge=float(req.get("initial_battery_fraction", 1.0)),
        solarPowerWatts=float(req.get("solar_power_watts", 0.0)),
        payloadProvider=sensor.payload_at,
    )

    initial_budget_m = float(req.get("budget_meters", DEFAULT_BUDGET_METERS))
    max_steps = int(req.get("max_simulation_steps", 20000))
    time_step_s = float(req.get("simulation_step_seconds", 10.0))
    if max_steps < 1 or time_step_s <= 0:
        raise ValueError("max_simulation_steps and simulation_step_seconds must be positive")
    pending_commands = [RerouteCommand(convertPlanToRoute(plan)), ModeChangeCommand(OperationalMode.NOMINAL)]
    current_plan = plan
    completed_subareas: set[str] = set()
    sampled_records: list[dict[str, Any]] = []
    telemetry_rows: list[dict[str, Any]] = []
    replan_events: list[dict[str, Any]] = []
    plan_history: list[dict[str, Any]] = [{"reason": "initial", "plan": plan_to_dict(plan)}]
    travelled_m = 0.0
    new_flagged_since_checkpoint = False
    refresh_forecast = bool(req.get("check_new_forecast", False))
    latest_product_time = initial_source_latest
    stop_reason = None

    for step_index in range(max_steps):
        inputs = simulator.currentInputs()
        outputs = software.step(inputs, pending_commands)
        pending_commands = []

        current_samples = []
        for frame in outputs.telemetryFrames:
            for sample_record in frame.samples:
                current_samples.append(sample_record)
            telemetry_rows.append({
                "time_seconds": frame.timeSeconds,
                "mode": frame.mode.value,
                "latitude": frame.position.latitude,
                "longitude": frame.position.longitude,
                "battery_fraction": frame.batteryStateOfCharge,
                "charging_power_watts": frame.chargingPowerWatts,
                "is_reduced": frame.isReduced,
                "samples_in_frame": len(frame.samples),
                "channel_statuses": frame.channelStatuses,
                "channel_values": frame.channelValues,
            })
        # Reporter.takeUnsentSamples() may have consumed the samples already; this retrieves
        # only anything still pending, so no sample is recorded twice.
        current_samples.extend(software.sampleManager.takeUnsentSamples())
        for sample_record in current_samples:
            validation_record = validator.add_sample(sample_record, mission_start)
            sampled_records.append(validation_record)
            if validation_record["flagged"]:
                new_flagged_since_checkpoint = True

        next_inputs = simulator.step(outputs, time_step_s)
        travelled_m += _rough_distance_m(inputs.position.latitude, inputs.position.longitude,
                                         next_inputs.position.latitude, next_inputs.position.longitude)

        # A monitoring target is complete after its ringExit waypoint has been passed.
        exit_indices = _route_exit_indices(current_plan)
        completed_now = [identifier for identifier, exit_index in exit_indices.items()
                         if identifier not in completed_subareas and software.routeExecutor.activeIndex > exit_index]
        for identifier in completed_now:
            completed_subareas.add(identifier)
            checkpoint_reason = None
            if new_flagged_since_checkpoint:
                checkpoint_reason = "divergence_flagged"

            if refresh_forecast:
                try:
                    if forecast_refresher is not None:
                        updated_dataset = forecast_refresher()
                    else:
                        updated_dataset = fetch(refresh=True)
                    updated_latest = str(updated_dataset.time.values.max())
                    if updated_latest > latest_product_time:
                        dataset = updated_dataset
                        validator.field_index = CopernicusFieldIndex(dataset, variable)
                        latest_product_time = updated_latest
                        checkpoint_reason = "new_forecast"
                except Exception as error:
                    replan_events.append({"time_seconds": simulator.timeSeconds,
                                          "reason": "forecast_check_failed", "message": str(error)})

            if checkpoint_reason:
                remaining = [subarea for subarea in subareas if subarea.identifier not in completed_subareas]
                remaining_budget = max(0.0, initial_budget_m - travelled_m)
                if remaining and remaining_budget > 1.0:
                    current_position = GeoPoint(inputs.position.latitude, inputs.position.longitude)
                    replan = planRoute(
                        area, remaining, current_position,
                        budget=Budget(maxDistanceMeters=remaining_budget),
                        sampling=SamplingSpec(bufferMeters=float(req.get("buffer_meters", 50.0)),
                                              sampleSpacingMeters=spacing_m),
                        cruiseSpeedMetersPerSecond=speed_mps,
                        isAllowed=coast_guard.is_allowed if coast_guard else None,
                        isLegAllowed=coast_guard.segment_allowed if coast_guard else None,
                    )
                    event = {
                        "time_seconds": simulator.timeSeconds,
                        "reason": checkpoint_reason,
                        "triggered_after_subarea": identifier,
                        "remaining_subareas": [entry.identifier for entry in remaining],
                        "remaining_budget_m": remaining_budget,
                        "position": _position_dict(inputs.position),
                        "plan_status": replan.status.value,
                    }
                    if replan.status == RouteStatus.INFEASIBLE or not replan.waypoints:
                        event["message"] = "Replan infeasible; mission stopped rather than execute an unvalidated route."
                        replan_events.append(event)
                        stop_reason = "replan_infeasible"
                        break
                    event["new_route_distance_m"] = replan.totalDistanceMeters
                    replan_events.append(event)
                    current_plan = replan
                    plan_history.append({"reason": checkpoint_reason, "plan": plan_to_dict(replan)})
                    pending_commands = [RerouteCommand(convertPlanToRoute(replan))]
                    if software.mode != OperationalMode.NOMINAL:
                        pending_commands.append(ModeChangeCommand(OperationalMode.NOMINAL))
                new_flagged_since_checkpoint = False

        if stop_reason:
            break
        if software.routeExecutor.isRouteComplete and not pending_commands:
            stop_reason = "route_complete"
            break
    else:
        stop_reason = "max_simulation_steps_reached"

    # Include a final position and battery snapshot even if the last full telemetry interval has not elapsed.
    final_inputs = simulator.currentInputs()
    cell_summary = validator.summary()
    result_plan = plan_to_dict(current_plan)
    result = {
        "run_id": run_id,
        "status": "complete" if stop_reason == "route_complete" else ("stopped" if stop_reason == "replan_infeasible" else "incomplete"),
        "output_dir": str(run_dir),
        "mission_start_utc": mission_start.isoformat(),
        "mission_elapsed_seconds": simulator.timeSeconds,
        "simulator_steps": step_index + 1,
        "distance_travelled_m": travelled_m,
        "remaining_budget_m": max(0.0, initial_budget_m - travelled_m),
        "final_position": _position_dict(final_inputs.position),
        "final_battery_fraction": final_inputs.batteryStateOfCharge,
        "final_mode": software.mode.value,
        "stop_reason": stop_reason,
        "sensor_source": source_label,
        "copernicus_latest_time_initial": initial_source_latest,
        "copernicus_latest_time_final": latest_product_time,
        "completed_subareas": sorted(completed_subareas),
        "replans": replan_events,
        "plan_history": plan_history,
        "final_plan": result_plan,
        "validation": cell_summary,
        "sample_count": len(sampled_records),
        "telemetry_frame_count": len(telemetry_rows),
        "threshold_mg_m3": threshold,
        "notes": [
            "Synthetic stand-in readings are for software demonstration only and are not in-situ observations.",
            "Copernicus comparison uses the nearest available product timestamp and containing grid cell.",
            "The land/coast test uses a coarse global land mask and is not a navigation safety certification.",
        ],
    }
    _write_csv(run_dir / "crosscheck_samples.csv", sampled_records)
    _write_csv(run_dir / "per_cell_validation.csv", cell_summary["cell_results"])
    _write_csv(run_dir / "telemetry.csv", telemetry_rows)
    _json_dump(run_dir / "replan_events.json", replan_events)
    _json_dump(run_dir / "plan_history.json", plan_history)
    _json_dump(run_dir / "summary.json", result)
    return result


def _rough_distance_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    mean_lat = math.radians((lat_a + lat_b) / 2.0)
    east = math.radians(lon_b - lon_a) * 6371000.0 * math.cos(mean_lat)
    north = math.radians(lat_b - lat_a) * 6371000.0
    return math.hypot(east, north)
