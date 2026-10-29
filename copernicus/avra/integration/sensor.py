"""Chlorophyll sensor providers for the simulator.

The default provider is a clearly labelled synthetic *stand-in dataset*: spatial stations are
written to CSV, seeded from the Copernicus field, then receive localised offsets so the full
validation/flagging path can be demonstrated. These are not real in-situ observations.
A user-supplied CSV can replace it without changing the mission driver.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from avra.copernicus.client import sample as sample_copernicus
from .planner_adapter import GeoPoint, SubArea
from .validator import CopernicusFieldIndex


@dataclass(frozen=True)
class SensorStation:
    station_id: str
    latitude: float
    longitude: float
    chlorophyll_mg_m3: float
    source: str = "synthetic_standin"


def _distance_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    mean_lat = math.radians((lat_a + lat_b) / 2.0)
    east = math.radians(lon_b - lon_a) * 6371000.0 * math.cos(mean_lat)
    north = math.radians(lat_b - lat_a) * 6371000.0
    return math.hypot(east, north)


def build_standin_dataset(output_path: str | Path, dataset, subareas: list[SubArea], variable: str,
                          when: datetime, grid_size: int = 5) -> list[SensorStation]:
    """Build a repeatable synthetic station dataset around selected monitoring zones.

    Values = Copernicus baseline at the station (when available) + a smooth localised anomaly
    + small deterministic fixture noise. Offsets are for software demonstration, not science.
    """
    if grid_size < 2:
        raise ValueError("grid_size must be at least 2")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    field_index = CopernicusFieldIndex(dataset, variable)
    time_index = field_index.index_for(when)
    field = field_index.field(time_index)
    anomalies = (0.36, -0.29, 0.23, -0.19, 0.31, -0.25)
    stations: list[SensorStation] = []
    csv_rows = []
    station_number = 0

    for area_index, subarea in enumerate(subareas):
        latitudes = [corner.latitude for corner in subarea.corners]
        longitudes = [corner.longitude for corner in subarea.corners]
        lat_min, lat_max = min(latitudes), max(latitudes)
        lon_min, lon_max = min(longitudes), max(longitudes)
        latitude_padding = 120.0 / 111195.0
        longitude_padding = 120.0 / (111195.0 * max(0.1, math.cos(math.radians((lat_min + lat_max) / 2))))
        lat_min, lat_max = lat_min - latitude_padding, lat_max + latitude_padding
        lon_min, lon_max = lon_min - longitude_padding, lon_max + longitude_padding
        center_lat = sum(latitudes) / len(latitudes)
        center_lon = sum(longitudes) / len(longitudes)
        amplitude = anomalies[area_index % len(anomalies)]
        spread_m = max(180.0, _distance_m(lat_min, lon_min, lat_max, lon_max) * 0.55)

        for row in range(grid_size):
            latitude = lat_min + (lat_max - lat_min) * row / (grid_size - 1)
            for column in range(grid_size):
                longitude = lon_min + (lon_max - lon_min) * column / (grid_size - 1)
                reference = sample_copernicus(field, latitude, longitude, fallback_cells=0)
                baseline = float(reference.value) if reference.status == "ok" and math.isfinite(reference.value) else 0.45
                distance = _distance_m(latitude, longitude, center_lat, center_lon)
                local_anomaly = amplitude * math.exp(-0.5 * (distance / spread_m) ** 2)
                # Deterministic, low-amplitude noise makes the fixture non-constant without a sine-wave simulator.
                noise = (((station_number * 37 + row * 11 + column * 17) % 13) - 6) * 0.004
                value = max(0.01, baseline + local_anomaly + noise)
                station = SensorStation(f"{subarea.identifier}-S{row:02d}{column:02d}",
                                        latitude, longitude, value)
                stations.append(station)
                csv_rows.append({
                    "station_id": station.station_id,
                    "time_utc": when.astimezone(timezone.utc).isoformat(),
                    "latitude": f"{latitude:.7f}",
                    "longitude": f"{longitude:.7f}",
                    "chlorophyll_mg_m3": f"{value:.6f}",
                    "source": "SYNTHETIC_STANDIN_NOT_MEASURED",
                    "copernicus_baseline_mg_m3": f"{baseline:.6f}",
                    "local_anomaly_mg_m3": f"{local_anomaly:.6f}",
                    "reference_status_at_station": reference.status,
                    "target_id": subarea.identifier,
                })
                station_number += 1

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]) if csv_rows else [
            "station_id", "time_utc", "latitude", "longitude", "chlorophyll_mg_m3", "source",
            "copernicus_baseline_mg_m3", "local_anomaly_mg_m3", "reference_status_at_station", "target_id"])
        writer.writeheader()
        writer.writerows(csv_rows)
    return stations


class CsvStandInSensor:
    """IDW interpolation over a CSV of sensor stations; real station CSVs can use same schema.

    Required columns: latitude, longitude, chlorophyll_mg_m3. Extra metadata columns are ignored.
    """

    def __init__(self, stations: Iterable[SensorStation], neighbours: int = 4,
                 exact_hit_radius_m: float = 1.0):
        self.stations = list(stations)
        if not self.stations:
            raise ValueError("sensor dataset contains no stations")
        self.neighbours = max(1, int(neighbours))
        self.exact_hit_radius_m = float(exact_hit_radius_m)
        self.source = self.stations[0].source

    @classmethod
    def from_csv(cls, path: str | Path, neighbours: int = 4) -> "CsvStandInSensor":
        stations: list[SensorStation] = []
        with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required = {"latitude", "longitude", "chlorophyll_mg_m3"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"sensor CSV missing required columns: {sorted(missing)}")
            for index, row in enumerate(reader):
                try:
                    stations.append(SensorStation(
                        station_id=row.get("station_id") or f"CSV-{index:05d}",
                        latitude=float(row["latitude"]), longitude=float(row["longitude"]),
                        chlorophyll_mg_m3=float(row["chlorophyll_mg_m3"]),
                        source=row.get("source") or "csv_sensor_dataset",
                    ))
                except (TypeError, ValueError):
                    continue
        return cls(stations, neighbours=neighbours)

    def value_at(self, latitude: float, longitude: float) -> float:
        distances = [(_distance_m(latitude, longitude, station.latitude, station.longitude), station)
                     for station in self.stations]
        distances.sort(key=lambda item: item[0])
        nearest = distances[:self.neighbours]
        if nearest[0][0] <= self.exact_hit_radius_m:
            return nearest[0][1].chlorophyll_mg_m3
        weights = [1.0 / max(distance, 10.0) ** 2 for distance, _ in nearest]
        total_weight = sum(weights)
        return sum(weight * station.chlorophyll_mg_m3
                   for weight, (_, station) in zip(weights, nearest)) / total_weight

    def payload_at(self, latitude: float, longitude: float, time_seconds: float = 0.0) -> dict[str, float]:
        return {"chlorophyll": float(self.value_at(latitude, longitude)), "seaSurfaceTemperature": 20.0}
