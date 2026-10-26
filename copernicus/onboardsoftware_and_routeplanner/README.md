# Copernicus hackathon software (AVRA)

Python 3.9 or newer, standard library only (the plot script also needs matplotlib). Naming: camelCase and PascalCase, full words, no names of 3 letters or fewer, doxygen-style docstrings, no inline comments.

## How to run
All imports are flat (`from routePlanner import ...`, `from onboard.x import ...`). Keep every file of this folder together, with the `onboard/` folder next to `routePlanner.py`, and run every command from inside this folder. Do not rename `onboard/`.

```
python3 -m unittest -v testRoutePlanner     (12 tests)
python3 -m unittest -v testOnboard          (32 tests)
python3 demoRoutePlanner.py
python3 demoOnboard.py
python3 plotDemoRoutePlanner.py             (needs matplotlib, writes demoRoute.png)
```

## Route planner (off-board), frozen for now
- `routePlanner.py`: `planRoute(area, subAreas, startPoint, endPoint, budget, sampling, ...)`, returns a `RoutePlan` with waypoints. Rings around subareas, obstacle-aware transit, distance budget.
- `routePlannerDocumentation.md`: full documentation, assumptions and limitations (section 4).
- `testRoutePlanner.py`: 12 unit tests.
- `demoRoutePlanner.py`: demo on a 4 x 4 km area with six farms.
- `plotDemoRoutePlanner.py`: draws the demo route to `demoRoute.png`.

## Onboard software (abstract, mode state machine)
- `onboard/`: the package. `onboardSoftware.py` is the top level (`OnboardSoftware.step(inputs, telecommands)`), `modeManager.py` the state machine, plus health monitor, energy and drift assessors, route executor, sample manager, reporter, command handler, types, configuration, geometry, mode profile. It does not import the planner.
- `onboardDocumentation.md`: documentation, requirement coverage table, simulator usage guide, assumptions and limitations.
- `simulatorExample.py`: simple vessel simulator, `runSimulation()` loop and `convertPlanToRoute()` (the only link between planner and onboard software).
- `demoOnboard.py`: three scenarios (normal mission, low battery with solar recharge, thruster fault).
- `testOnboard.py`: 32 unit tests.

## Requirements
The requirement IDs in the code and documentation (MSN, PHY, PERF, OPS, FUN) refer to the project document "mission and system requirements", which is not part of this repository. The coverage table at the top of `onboardDocumentation.md` maps each onboard requirement to the module that implements it. Values marked TBC in the requirements (battery thresholds, drift threshold, telemetry period, sample spacing, channel limits) are placeholders in the code and live in `onboard/onboardConfiguration.py` and `onboard/healthMonitor.py`.
