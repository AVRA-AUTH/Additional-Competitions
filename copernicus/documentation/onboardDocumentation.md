# Onboard software documentation

Abstract onboard software of the ASV: mode state machine, health monitoring, route execution, sampling, telemetry and telecommands. It is plain Python (3.9 or newer, standard library only) with no firmware and no internal communication. It is driven by one `step()` call per simulator tick.

Files: `onboard/` (the package), `simulatorExample.py`, `demoOnboard.py`, `testOnboard.py`. The demo and the example simulator also use the frozen planner (`routePlanner.py`, `demoRoutePlanner.py`). Run all commands from the folder that contains these files.

Requirement IDs refer to the project document "mission and system requirements" (revision of 2026-10-26), which is not part of this repository.

---

## 1. Scope and requirement coverage

| Requirement | Where it is implemented |
| --- | --- |
| FUN-1 (onboard scope) | The whole package: route execution, sampling, health monitoring, telemetry |
| FUN-2 route execution | `routeExecutor.py` |
| FUN-3 sampling trigger | `sampleManager.py` |
| FUN-4 health monitoring | `healthMonitor.py` (channel table, limits, fault table; limits are placeholders) |
| OPS-1 to OPS-5 modes and transitions | `modeManager.py`, `modeProfile.py` |
| OPS-6 safe exit inhibit (every exit from Safe) | `modeManager.py` |
| OPS-7 failure sets | `healthMonitor.py`: the fault table is data, to be filled after the FMEA |
| OPS-8, OPS-9 battery thresholds | `modeManager.py`, `driftAssessor.py` |
| OPS-10 isolation independence | `healthMonitor.py` and the `isolatedSubsystems` output |
| OPS-11 to OPS-13 telemetry and telecommand | `reporter.py`, `commandHandler.py` |
| OPS-14 energy inhibit | `energyAssessor.py`, `modeManager.py` |
| OPS-15 Safe exit after low battery (70%, TBC) | `modeManager.py` (`safeExitBatteryFraction`) |
| OPS-16 autonomous return to Nominal | `modeManager.py` (`evaluateAutonomousTransition`, `standbyEnteredForLowBattery`) |
| OPS-17 end of route to Standby | `onboardSoftware.py` (second autonomous check in `step()`), `modeManager.py` |
| OPS-18 self-test dependency per mode | `modeProfile.py` (`modeRequiredSelfTests`), `healthMonitor.py`, `modeManager.py` |
| OPS-19 reroute execution | `commandHandler.py`, `routeExecutor.py` |
| OPS-20 telemetry content by mode | `reporter.py` |
| MSN-9 mission success, MSN-12 recoverability | Telemetry content and the reduced safe-mode frame |

## 2. Modules

| Module | Job |
| --- | --- |
| `onboardTypes.py` | Enumerations and data classes (inputs, outputs, telecommands, telemetry) |
| `onboardConfiguration.py` | All thresholds and parameters in one dataclass |
| `onboardGeometry.py` | Local flat-frame distance and bearing helpers |
| `modeProfile.py` | Functions enabled per mode, and the self-tests each mode depends on |
| `healthMonitor.py` | Channel table, limits, fault responses, self-test results |
| `energyAssessor.py` | Can the remaining energy cover the remaining route (OPS-14) |
| `driftAssessor.py` | `calculateDrift()` (reads the simulator value) and the drift threshold |
| `modeManager.py` | The state machine: guards, autonomous triggers, transition log |
| `routeExecutor.py` | Waypoint following with line-of-sight guidance and progress tracking |
| `sampleManager.py` | One sample per sample spacing on sampling legs, interpolated between ticks |
| `reporter.py` | Periodic and on-demand telemetry frames |
| `commandHandler.py` | The three telecommands: mode change, data request, reroute |
| `onboardSoftware.py` | Top level, `OnboardSoftware.step(inputs, telecommands)` |

## 3. Mode state machine

### Functions per mode
| Mode | Enabled | Disabled |
| --- | --- | --- |
| NOMINAL | everything | nothing |
| STANDBY | telemetry, telecommand, navigation | propulsion, route execution, sampling |
| SAFE | telemetry, telecommand, navigation | propulsion, route execution, sampling, faulted subsystems are isolated |

### Self-tests each mode depends on
| Self-test | Channels | NOMINAL | STANDBY | SAFE |
| --- | --- | --- | --- | --- |
| power | batteryCurrent, batteryTemperature | yes | yes | no |
| navigation | gpsFix | yes | yes | no |
| propulsion | thrusterStatus | yes | no | no |
| communication | communicationLink | yes | yes | no |

### Transition rules
| Transition | Rule |
| --- | --- |
| Any mode to SAFE | Always accepted |
| Leaving SAFE | Only by telecommand (never autonomous), the target mode's self-tests must pass |
| Leaving SAFE after a low-battery entry | Additionally needs battery at 70% or more |
| Entering NOMINAL or STANDBY | The target mode's self-tests must pass |
| Entering NOMINAL with a route pending | The route must be energy-feasible (OPS-14) |
| NOMINAL to STANDBY (autonomous) | Battery below 40% AND drift acceptable (OPS-8), or the route is complete |
| Any mode but SAFE to SAFE (autonomous) | Battery below 20% (OPS-9), or a fault in a self-test the current mode depends on |
| STANDBY to NOMINAL (autonomous) | Only if STANDBY was entered because of low battery, a route is pending and the battery is at 70% or more |
| Telecommand mode change | Same guards as above, any mode to any mode |

Within one tick the safe triggers are checked first, then the standby triggers.

### Other rules
- **Reroute** never changes the mode. The route is stored, and it is simply not executed while the ASV is not in NOMINAL.
- A reroute that arrives in NOMINAL and is not energy-feasible moves the ASV to STANDBY.
- **Feasibility (OPS-14):** the remaining route must be completable without the battery falling below the 20% safe threshold, using 165 W average power at 2 knots and a 2178 Wh capacity. If no route is pending, the check passes.
- **End of route:** automatic transition to STANDBY.
- **Drift:** `calculateDrift()` returns the value the simulator supplies. It is compared with the 0.5 m/s threshold (TBC).
- A paused route keeps its progress and resumes where it stopped.

### Status against the requirements
The decisions made while building this state machine (every exit from Safe by telecommand only and gated by the target mode's self-tests, the 70% levels, standby entered for low battery as the only case of autonomous return to Nominal, OPS-14 also applied to a reroute received in Nominal, a fault triggering Safe only when the current mode depends on the failing self-test) were written back into the requirements as OPS-5, OPS-6, OPS-14 to OPS-20 and FUN-4. The code and the requirements revision of 2026-10-26 describe the same behaviour. What remains open is listed in the requirements as TBC or deferred:
- the 70% levels (OPS-15, OPS-16), the 40% and 20% levels, the 0.5 m/s drift threshold and the telemetry period are placeholders
- the failure-mode sets (OPS-7) and the fault table content and channel limits (FUN-4) wait for the FMEA

## 4. Telemetry and telecommands

- **Period:** 600 s in every mode (TBC). The first frame is sent at the first tick.
- **NOMINAL frame:** position, battery state of charge and charging power, channel values and statuses, payload readings, unsent samples.
- **STANDBY frame:** the same without payload readings. Unsent samples are still reported, so data collected at the end of a route is not held back.
- **SAFE frame (reduced):** position, battery, channel values and statuses, mode. No payload readings and no samples (MSN-12).
- **Telecommands:** `ModeChangeCommand`, `DataRequestCommand` (answers at once with an on-demand frame, rejects unknown names), `RerouteCommand` (rejects an empty route). Every command gets a `CommandResponse` with a reason.

## 5. Using it with a simulator

### The interface
Every tick the simulator gives the onboard software a `VehicleInputs` and receives a `VehicleOutputs`.

| `VehicleInputs` field | Meaning |
| --- | --- |
| `timeSeconds`, `position` | Simulation time and position (`GeographicPosition`) |
| `headingDegrees`, `speedMetersPerSecond` | Vessel state |
| `hasGpsFix`, `thrusterIsOperational`, `communicationLinkIsUp` | Subsystem status |
| `batteryStateOfCharge` (0 to 1), `batteryCurrentAmperes`, `batteryTemperatureCelsius`, `chargingPowerWatts` | Power system |
| `simulatedDriftMetersPerSecond` | Drift without propulsion, change it whenever you like |
| `payloadReadings` | Payload values by name, for example chlorophyll |

| `VehicleOutputs` field | Meaning |
| --- | --- |
| `propulsionEnabled`, `desiredHeadingDegrees`, `desiredSpeedMetersPerSecond` | What the simulator should apply |
| `isolatedSubsystems` | Subsystems switched off in safe mode |
| `crossTrackErrorMeters` | Distance from the active leg |
| `telemetryFrames`, `commandResponses`, `modeTransitions` | Reports of this tick |
| `mode` | Mode after this tick |

### Minimal loop
```python
from onboard.onboardSoftware import OnboardSoftware
from onboard.onboardTypes import ModeChangeCommand, OperationalMode, RerouteCommand
from simulatorExample import SimpleVesselSimulator, convertPlanToRoute, runSimulation

software = OnboardSoftware(initialMode=OperationalMode.STANDBY)
simulator = SimpleVesselSimulator(startPosition)
route = convertPlanToRoute(plan)
result = runSimulation(
    software, simulator, durationSeconds=40000, timeStepSeconds=10,
    scheduledCommands=[(0.0, RerouteCommand(route)),
                       (10.0, ModeChangeCommand(OperationalMode.NOMINAL))])
```
`runSimulation()` wraps this loop, which a real simulator can do itself:
1. Build `VehicleInputs` from the simulator state.
2. Call `outputs = software.step(inputs, telecommands)` with the telecommands received since the last tick.
3. Apply `outputs.desiredHeadingDegrees` and `desiredSpeedMetersPerSecond` if `propulsionEnabled`, and advance the simulation by the time step.

### Connecting the planner
`convertPlanToRoute(plan)` turns a planner `RoutePlan` into the tuple of `RouteWaypoint(position, isSamplingLeg)` that a `RerouteCommand` carries. It is the only place where the planner and the onboard software meet.

### Changing the situation during a run
`scheduledActions` takes `(time, function)` pairs that modify the simulator, for example:
- `lambda vessel: setattr(vessel, "thrusterIsOperational", False)` injects a thruster fault.
- `lambda vessel: setattr(vessel, "driftMetersPerSecond", 0.9)` changes the drift.
- `lambda vessel: setattr(vessel, "solarPowerWatts", 150.0)` turns solar charging on.

### Configuration
All thresholds live in `OnboardConfiguration` (`standbyEntryBatteryFraction`, `safeEntryBatteryFraction`, `autonomousNominalReturnBatteryFraction`, `safeExitBatteryFraction`, `driftThresholdMetersPerSecond`, `telemetryPeriodSeconds`, sample spacing and others). Pass your own instance to `OnboardSoftware(configuration)`. The monitored channels and their limits are in `defaultChannelDefinitions()` in `healthMonitor.py`.

## 6. Demo and tests

Run `python3 demoOnboard.py`. It runs the planner route in the example simulator:
1. Normal mission, six farms, full battery: standby to nominal by telecommand, 5.7 h later back to standby at the end of the route, about 3050 samples.
2. Low battery: starts at 55%, drops below 40% after about 2 h and goes to standby, solar power starts at 2.5 h, the ASV returns to nominal on its own at 70% (about 7.6 h) and finishes the route.
3. Thruster fault after 2 h: nominal to safe, a telecommand to nominal is rejected (propulsion self-test failing), a telecommand to standby is accepted.

Run `python3 -m unittest -v testOnboard` for the 32 tests. They cover:
- mode profiles, battery thresholds with and without acceptable drift, the 20% safe entry
- fault handling and isolation, faults that do not matter for the current mode
- safe mode exit rules (self-tests, 70% after low battery, telecommand only)
- autonomous standby to nominal return, and the cases where it must not happen
- OPS-14 inhibit and the infeasible reroute
- telemetry period in every mode, the reduced safe frame, data requests, rejected commands
- guidance cross-track error, sampling only on sampling legs, sample spacing, pause and resume
- a full mission from a planner route in the simulator

## 7. Assumptions
- The drift for OPS-8 comes from the simulator. The threshold is 0.5 m/s (TBC).
- The battery thresholds (40%, 20%) and the 70% levels (OPS-15, OPS-16) are TBC values in the requirements. The 70% levels are decisions, not derived.
- The "safe state" for OPS-14 means finishing the route without dropping below the 20% safe threshold, after which the ASV waits in standby.
- Energy use is a constant 165 W at 2 knots with a 2178 Wh battery, taken from the power budget of the requirements.
- The six monitored channels (battery state of charge, current and temperature, GPS fix, thruster status, communication link) are examples, and their limits are placeholders. The communication link channel was added so the communication self-test has an input.
- The route arrives as a list of waypoints with a flag for sampled legs. It is stored as data, not code shared with the planner.
- The ASV starts in STANDBY at power-up.

## 8. Limitations
- The failure sets of OPS-7 are not defined. The fault table only has example entries until the FMEA is done. In the example table a communication link fault is report-only and does not trigger Safe mode.
- The drift calculation, the energy model and the vessel dynamics are simplified. There is no wind, current or sea-state model.
- No hysteresis or filtering on health channels: one bad reading is a fault. No stale-data timeouts.
- No telemetry bandwidth model: samples are sent in the next periodic frame and are assumed delivered, with no acknowledgement or retransmission.
- Guidance is simple line-of-sight. It is not tuned to the FUN-2 accuracy benchmark.
- Only the straight, convex-ring routes of the planner were tested.
- Replanning is by reroute telecommand only. The onboard side has no local path planner (FUN-1).
- The real telemetry and telecommand link is not implemented. Telecommands and frames are Python objects passed to and from `step()`.
