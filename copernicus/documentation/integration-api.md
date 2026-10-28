# AVRA integration API: implementation notes

## Boundary and data flow

`avra/integration/mission.py` is the application service shared by Dash, REST and the CLI. It converts UI zone dictionaries, invokes the existing planner, converts its `RoutePlan` with the existing `convertPlanToRoute`, injects a sensor provider into `SimpleVesselSimulator`, validates generated `SampleRecord`s against a cached Copernicus field, and saves a run directory.

```text
Dash / REST / CLI
       |
       v
 mission.py  <---- sensor.py (CSV or synthetic station stand-in)
   |   |  \
   |   |   +---- validator.py <---- Copernicus client/cache
   |   +-------- coast.py (point + route-segment checks)
   +------------ planner_adapter.py
                    |                 
                    v                 v
              routePlanner.py   onboard software + simulator
```

## Service functions

- `zones_to_planner_inputs(zones)` — input is a list of `{bbox, role, weight}` objects; output is the background polygon and weighted four-corner `SubArea`s.
- `create_plan(request, dataset=None, coast_guard=None)` — pure planning entry point used by `POST /api/plan` and the mission runner.
- `run_mission(request, output_root=None, dataset=None, coast_guard=None, sensor_csv=None)` — runs the lifecycle and writes auditable artifacts.
- `CrossValidator.add_sample(record, mission_start)` — selects the nearest available Copernicus time index, samples the containing grid cell without fallback, calculates signed/absolute differences and updates per-cell aggregates.

## Replanning behavior

The onboard executor continues until it has passed a target's `ringExit` waypoint. At that checkpoint, the mission service checks whether samples diverged or a newer forecast is available. It removes completed targets from the candidate list, subtracts travelled distance from the mission cap, and calls `planRoute` from the current position. A successful route is sent as a `RerouteCommand`; an infeasible route stops the run. This is intentionally an adapter-level behavior: the planner itself remains callable as a standalone library.
