"""Copernicus-vs-ASV validation at sample time/location with per-cell bias summaries."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Optional

import numpy as np

from avra.copernicus.client import sample as sample_copernicus


def _as_naive_datetime64(value: datetime | str | np.datetime64) -> np.datetime64:
    if isinstance(value, np.datetime64):
        return value
    if isinstance(value, datetime):
        when = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
        return np.datetime64(when.isoformat(timespec="seconds"))
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return np.datetime64(parsed.isoformat(timespec="seconds"))


class CopernicusFieldIndex:
    """Cache time slices so each sample compares against the nearest available product time."""

    def __init__(self, dataset, variable: str):
        if variable not in dataset.data_vars:
            raise ValueError(f"Copernicus variable {variable!r} not found; available: {list(dataset.data_vars)}")
        self.dataset = dataset
        self.variable = variable
        self.times = dataset.time.values
        self._fields: dict[int, Any] = {}
        self._values: dict[int, np.ndarray] = {}

    def index_for(self, when: datetime | str | np.datetime64) -> int:
        requested = _as_naive_datetime64(when)
        delta = np.abs((self.times - requested).astype("timedelta64[s]").astype(float))
        return int(np.argmin(delta))

    def field(self, index: int):
        if index not in self._fields:
            da = self.dataset[self.variable]
            if "depth" in da.dims:
                da = da.isel(depth=0)
            self._fields[index] = da.isel(time=index).transpose("latitude", "longitude")
            self._values[index] = self._fields[index].values
        return self._fields[index]

    def metadata(self, index: int) -> dict[str, Any]:
        return {"variable": self.variable, "product_time": str(self.times[index]), "time_index": index}


@dataclass
class CellAggregate:
    cell_id: str
    cell_bounds: list[list[float]]
    count: int = 0
    flagged_samples: int = 0
    sum_sensor: float = 0.0
    sum_reference: float = 0.0
    sum_bias: float = 0.0
    sum_abs_bias: float = 0.0
    sum_squared_bias: float = 0.0

    def add(self, sensor_value: float, reference_value: float, flagged: bool) -> None:
        difference = sensor_value - reference_value
        self.count += 1
        self.flagged_samples += int(flagged)
        self.sum_sensor += sensor_value
        self.sum_reference += reference_value
        self.sum_bias += difference
        self.sum_abs_bias += abs(difference)
        self.sum_squared_bias += difference * difference

    def to_dict(self, threshold: float) -> dict[str, Any]:
        if not self.count:
            return {"cell_id": self.cell_id, "count": 0, "flagged": False}
        mean_sensor = self.sum_sensor / self.count
        mean_reference = self.sum_reference / self.count
        bias = self.sum_bias / self.count
        mae = self.sum_abs_bias / self.count
        rmse = math.sqrt(self.sum_squared_bias / self.count)
        flagged_fraction = self.flagged_samples / self.count
        cell_flagged = abs(bias) >= threshold or flagged_fraction >= 0.25
        return {
            "cell_id": self.cell_id,
            "cell_bounds": self.cell_bounds,
            "count": self.count,
            "mean_sensor_mg_m3": mean_sensor,
            "mean_copernicus_mg_m3": mean_reference,
            "bias_sensor_minus_copernicus_mg_m3": bias,
            "mae_mg_m3": mae,
            "rmse_mg_m3": rmse,
            "flagged_samples": self.flagged_samples,
            "flagged_fraction": flagged_fraction,
            "threshold_mg_m3": threshold,
            "flagged": cell_flagged,
        }


class CrossValidator:
    """Validate raw simulator samples against the Copernicus grid cell/time."""

    def __init__(self, dataset, variable: str = "chl", threshold_mg_m3: float = 0.15,
                 fallback_cells: int = 0, sensor_source: str = "standin_dataset"):
        if not math.isfinite(float(threshold_mg_m3)) or threshold_mg_m3 <= 0:
            raise ValueError("threshold_mg_m3 must be a finite positive number")
        self.field_index = CopernicusFieldIndex(dataset, variable)
        self.threshold_mg_m3 = threshold_mg_m3
        self.fallback_cells = fallback_cells
        self.sensor_source = sensor_source
        self.records: list[dict[str, Any]] = []
        self.cells: dict[str, CellAggregate] = {}
        self.global_sums = {"count": 0, "flagged_samples": 0, "sum_bias": 0.0,
                            "sum_abs_bias": 0.0, "sum_squared_bias": 0.0}

    def add_sample(self, sample_record, mission_start: datetime) -> dict[str, Any]:
        position = sample_record.position
        measurement_time = mission_start + timedelta(seconds=sample_record.timeSeconds)
        time_index = self.field_index.index_for(measurement_time)
        field = self.field_index.field(time_index)
        reference = sample_copernicus(field, position.latitude, position.longitude,
                                      fallback_cells=self.fallback_cells)
        payload = dict(sample_record.readings or {})
        sensor_value = payload.get("chlorophyll", payload.get("chl"))
        try:
            sensor_value = float(sensor_value)
        except (TypeError, ValueError):
            sensor_value = float("nan")
        status = reference.status
        copernicus_value = float(reference.value)
        valid = math.isfinite(sensor_value) and math.isfinite(copernicus_value) and status == "ok"
        cell_id = None
        if reference.bounds:
            south, west = reference.bounds[0]
            north, east = reference.bounds[1]
            cell_id = f"{(south + north) / 2:.5f}:{(west + east) / 2:.5f}"
        difference = sensor_value - copernicus_value if valid else float("nan")
        flagged = bool(valid and abs(difference) >= self.threshold_mg_m3)
        record = {
            "sample_index": len(self.records),
            "measurement_time_utc": measurement_time.astimezone(timezone.utc).isoformat(),
            "sim_time_seconds": float(sample_record.timeSeconds),
            "latitude": float(position.latitude),
            "longitude": float(position.longitude),
            "sensor_chlorophyll_mg_m3": sensor_value,
            "copernicus_chlorophyll_mg_m3": copernicus_value,
            "copernicus_product_time": str(self.field_index.times[time_index]),
            "copernicus_time_index": time_index,
            "copernicus_status": status,
            "copernicus_cell_id": cell_id,
            "copernicus_cell_bounds": reference.bounds,
            "sensor_minus_copernicus_mg_m3": difference,
            "absolute_difference_mg_m3": abs(difference) if valid else float("nan"),
            "threshold_mg_m3": self.threshold_mg_m3,
            "flagged": flagged,
            "comparable": valid,
            "sensor_source": payload.get("sensor_source", self.sensor_source),
            "sensor_station_distance_m": payload.get("sensor_station_distance_m"),
        }
        self.records.append(record)
        if valid and cell_id is not None:
            cell = self.cells.get(cell_id)
            if cell is None:
                cell = CellAggregate(cell_id, reference.bounds)
                self.cells[cell_id] = cell
            cell.add(sensor_value, copernicus_value, flagged)
            self.global_sums["count"] += 1
            self.global_sums["flagged_samples"] += int(flagged)
            self.global_sums["sum_bias"] += difference
            self.global_sums["sum_abs_bias"] += abs(difference)
            self.global_sums["sum_squared_bias"] += difference * difference
        return record

    def aggregate_rows(self) -> list[dict[str, Any]]:
        return [cell.to_dict(self.threshold_mg_m3) for cell in self.cells.values()]

    def summary(self) -> dict[str, Any]:
        count = self.global_sums["count"]
        rows = self.aggregate_rows()
        if count:
            bias = self.global_sums["sum_bias"] / count
            mae = self.global_sums["sum_abs_bias"] / count
            rmse = math.sqrt(self.global_sums["sum_squared_bias"] / count)
        else:
            bias = mae = rmse = None
        return {
            "samples_total": len(self.records),
            "samples_comparable": count,
            "samples_uncomparable": len(self.records) - count,
            "samples_flagged": self.global_sums["flagged_samples"],
            "global_bias_sensor_minus_copernicus_mg_m3": bias,
            "global_mae_mg_m3": mae,
            "global_rmse_mg_m3": rmse,
            "threshold_mg_m3": self.threshold_mg_m3,
            "cells_compared": len(rows),
            "cells_flagged": sum(bool(row.get("flagged")) for row in rows),
            "cell_results": rows,
        }
