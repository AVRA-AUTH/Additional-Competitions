# AVRA Copernicus coastal-monitoring demo

This project connects the Dash/Copernicus map to the existing AVRA route planner and onboard simulator. The UI and REST API now use one mission service, so the same zone-selection, planning, sampling, validation and replanning flow can run from the browser or from a script.

## Quick start

Use Python 3.11+ in a virtual environment, then install the project requirements:

```bash
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -r requirements.txt
copernicusmarine login                # needed only when the data cache must be fetched/refreshed
```

The supplied repository includes a Copernicus cache. Launch the UI with:

```bash
python -m avra.ui.app
```

Open `http://127.0.0.1:8050`, draw a background rectangle first, add one or more monitoring rectangles, set a launch pin, and press Start. The mission uses the coarse global land mask and requires all route and sampling legs to pass the 3 km coast check. If the planner cannot find a compliant route it stops and displays the reason rather than executing an unvalidated route.

## Run the end-to-end driver

From this `copernicus/` directory:

```bash
python -m scripts.run_mission --request examples/thermaikos_mission.json
```

That executes **fetch → zone conversion → plan → simulated vessel → cross-check → checkpoint replan**, then writes a timestamped folder under `runs/`. Copernicus data are fetched from the local cache by default. Set `"refresh_copernicus": true` in the request to refresh at mission start, or `"check_new_forecast": true` to check for a newer product at target-completion checkpoints.

The included Thermaikos Gulf scenario is seeded from published mussel-farming study sites (Kavoura/Chalastra, Axios delta and Gallikos estuary). Its rectangles are *approximate demo boxes* around the published sampling coordinates, **not** surveyed farm-lease polygons, legal boundaries or navigation waypoints. Verify the coastline, land mask, local restrictions and exact launch position before any real-world use. Coordinates in the article: [PMC study on Thermaikos Gulf mussel-farming sampling sites](https://pmc.ncbi.nlm.nih.gov/articles/PMC7949245/).

## REST API

The UI registers the same Flask blueprint on its server. It can also run standalone:

```bash
python -m avra.integration.api
```

The standalone API listens on `http://127.0.0.1:8060`.

- `GET /api/health` — cache, coast-mask and service status.
- `POST /api/plan` — validate the request and return the route plus normalized planner inputs. No simulation is run.
- `POST /api/run` — run the full mission and return its summary.
- `GET /api/runs/<run_id>` — read the summary saved under `runs/<run_id>/summary.json`.

Example planning request:

```bash
curl -X POST http://127.0.0.1:8060/api/plan \
  -H 'Content-Type: application/json' \
  --data @examples/thermaikos_mission.json
```

To run the full loop instead, use the same body at `/api/run`.

### Request fields

```json
{
  "zones": [
    {"bbox": [22.64, 40.47, 22.94, 40.65], "role": "background", "weight": 1},
    {"bbox": [22.764, 40.541, 22.770, 40.547], "role": "priority", "weight": 2}
  ],
  "start": {"lat": 40.516, "lon": 22.790},
  "budget_meters": 30000,
  "buffer_meters": 50,
  "sample_spacing_meters": 20,
  "cruise_speed_mps": 1.028888,
  "coast_rule_km": 3,
  "enforce_coast_rule": true,
  "divergence_threshold_mg_m3": 0.15,
  "variable": "chl",
  "refresh_copernicus": false,
  "check_new_forecast": false
}
```

Each `bbox` is `[west_lon, south_lat, east_lon, north_lat]`. The background rectangle defines the planning area; every other rectangle is a target. `weight` is passed to `SubArea.priority`. Repeated identical rectangles are collapsed and their weights summed. With the current planner, priority primarily affects which targets are dropped when the distance budget cannot cover all targets; it does not yet allocate a denser sampling pattern inside a higher-priority rectangle.

## Sensor input and validation

By default, the mission writes `in_situ_standin.csv`. It is a deterministic synthetic fixture: station values start from Copernicus cell values, then add small local anomalies. It is deliberately labelled **synthetic / not measured**; it demonstrates the data flow but is not scientific evidence or field data.

To use your own in-situ dataset, supply a CSV:

```bash
python -m scripts.run_mission \
  --request examples/thermaikos_mission.json \
  --sensor-csv path/to/observations.csv
```

Required columns are `latitude`, `longitude`, and `chlorophyll_mg_m3`. Optional fields such as `station_id`, `time_utc` and `source` are kept for provenance but are not yet used to time-interpolate the sensor dataset. During simulation, the provider interpolates from the nearest stations; to use a live sensor, implement the `payload_at(latitude, longitude, time_seconds)` interface in `avra/integration/sensor.py` and inject it in `run_mission`.

Each onboard sample is compared with the **containing Copernicus grid cell** at the nearest available product time. There is no nearest-cell fallback in the validator: samples outside the field or on a masked cell are recorded as uncomparable. The default point threshold is `0.15 mg/m³`. Outputs include sensor-minus-product bias, MAE, RMSE, the fraction of flagged samples and a per-cell flag. A cell is flagged when absolute mean bias reaches the threshold or at least 25% of that cell's comparable samples are flagged.

## Run outputs

Each run writes a single `runs/<run_id>/` folder. Key files:

| File | Contents |
|---|---|
| `mission_request.json` | Original request and mission timestamp |
| `planning_inputs.json` | Normalized start, area, target corners, priority and budgets |
| `initial_plan.json` | Initial planner result, waypoint list, distance and warnings |
| `copernicus_metadata.json` | Dataset ID, variable, coordinate/time extent and comparison rule |
| `in_situ_standin.csv` | Synthetic station fixture (omitted as a generated file when using your supplied CSV) |
| `crosscheck_samples.csv` | Per-sample sensor value, product value/time/cell, difference and flag |
| `per_cell_validation.csv` | Per-cell sample count, mean bias, MAE/RMSE, flag fraction and flag |
| `telemetry.csv` | Periodic simulated position, battery, mode and onboard channel values/status |
| `replan_events.json` | Replan reason, current position, completed target and remaining budget |
| `plan_history.json` | Initial route followed by every accepted replan |
| `summary.json` | Mission status, final vessel snapshot, aggregate validation statistics and artifact path |

Replanning happens at a target-completion checkpoint if a divergence has been flagged since the previous checkpoint or a newer forecast is detected. The planner is called with the current vessel position, remaining target IDs and remaining distance budget. If that plan is infeasible, the run stops rather than issuing the unsafe/invalid route.

## Important limitations

- The vessel and sensor transport are still simulated. A real onboard adapter should publish the same position, battery, health, mode and payload fields.
- The land mask is coarse and is **not** a nautical chart, obstacle database, or safety certification. Segment sampling reduces the risk of planner legs crossing a land-mask feature, but cannot make the route safe for a real vessel.
- If the straight-line visibility graph has no compliant route, the planner can return infeasible. It does not yet generate arbitrary coastline-following detour nodes.
- Sensor CSV interpolation currently ignores timestamp matching, calibration metadata and uncertainty. Real sampling deployments need those policies and sensor-specific QA/QC.
- Forecast refresh is opt-in because it may trigger a network request and data download.
- The UI's mode selector is still a display/command stub; mission runs start in Standby and command Nominal to execute the route. Automatic FDIR transitions in the onboard software remain active during simulation.
- A simulation pass does not authorise real vessel navigation. Review geofences, farm boundaries, local regulations, power/endurance and human override before any deployment.

## Tests

From `copernicus/`:

```bash
python -m unittest discover -s tests -v
cd onboardsoftware_and_routeplanner
python -m unittest -v testRoutePlanner testOnboard
```

The integration tests use an in-memory synthetic xarray dataset and explicitly disable the real coast mask for the end-to-end software test. The production driver defaults to the land/coast check.
