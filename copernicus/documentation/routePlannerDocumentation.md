# Route planner documentation

Files covered: `routePlanner.py`, `testRoutePlanner.py`, `demoRoutePlanner.py`, `plotDemoRoutePlanner.py`.
Python 3.9 or newer. The planner and the tests use only the standard library. The plot script needs matplotlib. Run all commands from the folder that contains these files.

---

## 1. routePlanner.py

### Purpose
Off-board route planning for the demo. Given a user-defined area and the subareas to monitor (for example aquaculture sites), `planRoute()` returns an ordered list of GPS waypoints. The vessel circles each subarea on a buffer ring, stays within a distance budget and never crosses a subarea.

### Quick use
```python
from routePlanner import GeoPoint, SubArea, planRoute

area = [GeoPoint(...), GeoPoint(...), GeoPoint(...), GeoPoint(...)]
subAreas = [SubArea("farmA", [GeoPoint(...), ...])]
plan = planRoute(area, subAreas, startPoint=GeoPoint(...), endPoint=GeoPoint(...))
```

### Inputs of planRoute()
| Parameter | Meaning |
| --- | --- |
| `area` | Four GPS corners of the area of interest (any order, must be convex) |
| `subAreas` | List of `SubArea(identifier, corners, priority)`, four GPS corners each |
| `startPoint` | Any position the route begins from (launch point or last reported position). May be slightly outside the area |
| `endPoint` | Optional. If given, the route finishes there. If omitted, the mission ends where the sampling ends: at the last sampled point of the last ring, with no transit afterwards |
| `budget` | `Budget`, default 30 km. `Budget.fromEnergy(wattHours, watts)` builds one from energy |
| `sampling` | `SamplingSpec`, default 50 m ring buffer and 5 m sample spacing |
| `cruiseSpeedMetersPerSecond` | Used for the duration estimate, default 2 knots |
| `maxTransitOffsetMeters` | How far start and end may be from the area, default 2000 m |
| `isAllowed` | Optional hook `GeoPoint -> bool` for a land mask or coast-distance check |

### Output: RoutePlan
| Field | Meaning |
| --- | --- |
| `status` | `COMPLETE` (all covered), `PARTIAL` (some skipped), `INFEASIBLE` (no route) |
| `waypoints` | Ordered `Waypoint(latitude, longitude, kind, subAreaIdentifier, isSamplingLeg)` |
| `totalDistanceMeters`, `transitDistanceMeters`, `samplingDistanceMeters` | Route lengths |
| `estimatedDurationSeconds`, `estimatedSampleCount` | Time and number of samples |
| `coveredSubAreaIdentifiers` | Subareas in visiting order |
| `skippedReasons` | Skipped subarea and the reason |
| `warnings` | Remarks, or the failure reason when infeasible |

Waypoint kinds: `start`, `transit`, `ringEntry`, `ringCorner`, `ringExit`, `end`.
A waypoint with `isSamplingLeg = True` means the leg that arrives at it is sampled.
Without an `endPoint` there is no `end` waypoint: the last waypoint is the `ringExit` of the last ring (a ring is entered and left at the same corner).
Skip reasons: `outsideArea`, `exceedsBudget`, `invalidGeometry`, `violatesConstraint`.

### How it works
1. Convert all GPS points to a local metric frame (`LocalFrame`, accurate at kilometre scale).
2. Check that the area is a valid convex shape and that start and end are close enough to it.
3. Build one ring per subarea: the subarea quadrilateral offset outward by the buffer (`offsetConvexPolygon`).
4. Build a visibility graph (`LegGraph`) from the start, the end and every ring corner. A leg is allowed only if it does not pass through the inside of any ring. Shortest paths come from Floyd-Warshall.
5. Choose the visiting order (`optimizeOrder`): nearest neighbour, then 2-opt. `evaluateOrder` picks the best entry corner per ring. Without an end point no return transit is counted, so the planner prefers an order that finishes cheaply at the last ring.
6. If the route is longer than the budget, `dropUntilWithinBudget` removes the subarea that saves the most distance per unit of priority, until it fits.
7. Assemble the waypoints. Transit legs that detour around a farm add `transit` waypoints.

### Sample points
The waypoint list holds corners only. `sampleSchedule(plan, spacingMeters)` expands the sampled legs into explicit sample points (every 5 m by default), which is the view of "a sequence of GPS points".

### Assumptions and limitations
See section 4 at the end of this file.

---

## 2. testRoutePlanner.py

### Purpose
Automated checks of `routePlanner.py` with the standard `unittest` module.

### Run
```
python3 -m unittest -v testRoutePlanner
```
Expected: `Ran 12 tests`, `OK`.

### Test setup
A 4 x 4 km area centred on a local frame at 40.30 N, 23.00 E, with the start at the south-west corner. Subareas are squares made by `squareAround(centerEast, centerNorth, sideMeters)`.

### The 12 tests
| Test | What it checks |
| --- | --- |
| `testSingleFarmRingLengthAndStatus` | One 400 m farm gives status `COMPLETE` and a ring of 4 x (400 + 2 x 50) = 2000 m |
| `testRouteStartsAndEndsAtGivenPoints` | First waypoint is `start`, last is `end` at the requested coordinates |
| `testWithoutEndPointRouteEndsAtLastRingExit` | With no end point the last waypoint is a sampled `ringExit` at the ring's entry corner and there is no `end` waypoint |
| `testWithoutEndPointIsShorterThanReturningToStart` | The open-ended route is shorter than the same route with `endPoint=startPoint` |
| `testBudgetDropsFarmsAndMarksPartial` | With a 4 km budget the far farm is skipped (`exceedsBudget`), status is `PARTIAL`, distance is at most 4 km |
| `testStartTooFarIsInfeasible` | A start 6 km from the area gives `INFEASIBLE` and a warning |
| `testSubAreaOutsideAreaIsSkippedWithWarning` | A subarea outside the area is skipped as `outsideArea`, a warning is added, it is not covered |
| `testRingLeavingAreaIsPlannedWithWarning` | A farm whose ring leaves the area is still planned and warns `extends outside` |
| `testCornerOrderDoesNotMatter` | The same corners given in a shuffled order give the same route length |
| `testTransitNeverCrossesAFarm` | With a large farm between start and end, no transit leg enters any farm polygon |
| `testSampleScheduleSpacing` | The number of sample points matches the sampled distance divided by 5 m |
| `testEnergyBudgetMatchesRequirement` | 2178 Wh at 165 W gives about 48.9 km, matching the PERF-5 power budget |

### Not covered
- Real coastlines or land masks (the `isAllowed` hook is not tested).
- Non-square or rotated subareas.
- Different priorities (all weights are equal).
- Very large problems (more than about 15 subareas). Runtime was measured only on up to 10.

---

## 3. demoRoutePlanner.py and plotDemoRoutePlanner.py

### demoRoutePlanner.py
Runs `planRoute()` on a 4 x 4 km (16 km^2) area with six aquaculture-sized farms (0.09 to 1.0 km^2), start at the south-west corner. The data is synthetic and the coordinates are around 40.30 N, 23.00 E.

Run:
```
python3 demoRoutePlanner.py
```

Scenarios printed, with the results seen in testing:
| Scenario | Result |
| --- | --- |
| Default budget (30 km), no end point | `complete`, all six farms, 21.0 km (5.8 transit, 15.3 sampling), 5.7 h, about 3050 samples. Ends at the last ring |
| Tight budget (15 km) | `partial`, farmA, farmB, farmF and farmD covered, 13.0 km, farmC and farmE skipped |
| Bigger buffer (200 m) | `complete`, all six farms, 27.7 km |
| Explicit end point (north-east corner) | `complete`, 23.1 km (7.8 transit), finishes at the given point |
| Start too far | `infeasible` with a warning (4000 m from the area, limit 2000 m) |
| Energy budget (2178 Wh, 165 W) | Same as the default budget case (21.0 km) |
| Farm whose ring leaves the area | `complete`, 7 farms, 24.3 km, warning `ring of farmEdge extends outside the area` |

The module also exposes `frame`, `area`, `subAreas` and `startPoint` so the plot script can reuse them. The scenarios run only when the file is executed directly.

### plotDemoRoutePlanner.py
Draws the default demo route so it can be checked by eye. Needs matplotlib.

Run:
```
python3 plotDemoRoutePlanner.py
```
Writes `demoRoute.png` in the current folder.

What the picture shows:
- Dashed grey rectangle: the area of interest.
- Orange squares: the farms, labelled by identifier.
- Blue lines: sampling rings (sampled legs).
- Green lines: transit legs.
- Black star: the start point.
- Title: total distance and plan status.

What to look for: every farm is surrounded by a blue ring, green lines never enter a farm or ring, and the route starts at the star and, because no end point is given, finishes at the last ring instead of returning.

---

## 4. Assumptions and limitations of the route planner

### Assumptions (taken as true for now)
**Inputs**
- The area of interest is defined by the user as four GPS corners. Anomaly detection does not choose it.
- The user places every subarea correctly inside the area. Each subarea is four GPS corners and convex.
- The UI only lets the user select sea. Every point that reaches the planner is sea, so the planner does not check land.
- The area stays within about 3 km of the coast (MSN-13, treated as a hard constraint). The UI is assumed to guarantee it, because the planner has no coastline data.
- Aquaculture sites are small (0.1 to 1 km^2) and the area is about 16 km^2.

**Sampling**
- Collecting data at a subarea means sampling on a ring around it at a buffer distance. The ASV never enters the farm itself (cages and moorings are assumed off limits).
- The buffer is 50 m. This is a placeholder: there is no data supporting any single value.
- Samples are taken every few metres (5 m default) along the ring, because the route length is fixed by the ring anyway.
- The planner outputs only the corner waypoints. The onboard software triggers the samples on the legs flagged as sampling legs.

**Route and budget**
- The vessel moves in straight lines through open water. Wind, waves and currents are not modelled.
- The budget is a flat distance cap of 30 km (MSN-3). Margin is assumed to be inside that figure already. Solar charging is extra margin.
- Speed is fixed near 2 knots. The energy budget uses a constant average power (derived from 6 packs, about 2178 Wh, and 13.2 h raw at 2 knots, so about 165 W).
- All subareas have equal priority.
- `startPoint` is any position. It is not required to be a launch point.
- If no end point is given, the mission ends at the last sampled point of the last ring. The ASV can stay at sea between missions and does not have to be recovered after every mission.
- A ring that extends outside the area is still planned in full, with a warning. It is not clipped and not skipped.
- A subarea that lies outside the area is skipped with a warning.

**Software**
- Planning is off-board and the ASV only executes the route (FUN-1). The planner is written in Python.
- A local flat metric frame is accurate enough at kilometre scale.

### Limitations (decided to leave for later)
- **Land and coast distance:** only a hook (`isAllowed`) exists and it is checked on the start, the end and the ring corners, not on the legs between them. The 3 km coast rule is not enforced by the planner.
- **Ring inside the farm:** sampling inside the farm polygon is not possible. Whether the ASV can enter later is open.
- **Buffer and sample spacing:** values are placeholders and need real data.
- **User-selectable priorities:** the `priority` field exists but no input uses it yet.
- **Replanning:** planning from the vessel's current position is not supported by the planner. There is no input for already sampled subareas or for the remaining budget. A new route reaches the ASV as a reroute telecommand.
- **Anomaly-driven areas:** the planner does not propose areas from Copernicus data. This is only added if time allows.
- **Budget realism:** no reserve, wind, current or sea-state derating, and no use of the Copernicus current fields.
- **Telemetry and telecommand link:** the route is handed to the onboard software as a `RerouteCommand` (see `onboardDocumentation.md`), converted by `convertPlanToRoute()` in `simulatorExample.py`. The real communication link that would carry it is not implemented.
- **Real data:** only synthetic layouts have been tested.

### Limitations that come from the current implementation (not yet discussed)
- Subareas and the area must be convex. Other shapes are rejected as invalid.
- The visiting order uses a heuristic (nearest neighbour plus 2-opt) and is not guaranteed optimal. It was tested on up to 10 subareas.
- The last ring is chosen only by distance. Where the vessel would be safest to wait is not considered.
- Only ring corners are used for detours. A straight leg that cuts a corner may be slightly longer than the best possible.
- A start or end point lying inside a ring makes the plan infeasible.
- All rings of valid subareas block transit, even those that were skipped.
