"""
@file demoOnboard.py
@brief Demo: the planner route is executed by the onboard software inside the example simulator.
@details Run with: python3 demoOnboard.py
         Three scenarios: a normal mission, a low-battery pause with solar recharge, and a thruster fault.
"""
from demoRoutePlanner import area, frame, startPoint, subAreas
from onboard.onboardSoftware import OnboardSoftware
from onboard.onboardTypes import (GeographicPosition, ModeChangeCommand, OperationalMode,
                                  RerouteCommand)
from routePlanner import Budget, planRoute
from simulatorExample import SimpleVesselSimulator, SimulationResult, convertPlanToRoute, runSimulation

secondsPerHour = 3600.0


def printResult(title: str, software: OnboardSoftware, simulator: SimpleVesselSimulator,
                result: SimulationResult) -> None:
    print(f"--- {title}")
    for record in result.transitions:
        print(f"  t={record.timeSeconds / secondsPerHour:5.2f} h  {record.fromMode.value} -> "
              f"{record.toMode.value}  ({record.source.value}: {record.reason})")
    for response in result.commandResponses:
        if not response.isAccepted:
            print(f"  rejected {type(response.command).__name__}: {response.reason}")
    print(f"  samples collected={software.sampleManager.totalSampleCount}, "
          f"telemetry frames={len(result.telemetryFrames)}, "
          f"final mode={software.mode.value}, final charge={simulator.stateOfCharge:.2f}")


def buildScenario(plan, stateOfCharge, solarPowerWatts=0.0):
    simulator = SimpleVesselSimulator(
        GeographicPosition(plan.waypoints[0].latitude, plan.waypoints[0].longitude),
        stateOfCharge=stateOfCharge, solarPowerWatts=solarPowerWatts)
    software = OnboardSoftware(initialMode=OperationalMode.STANDBY)
    return software, simulator


def commandsToStart(plan):
    return [(0.0, RerouteCommand(convertPlanToRoute(plan))),
            (10.0, ModeChangeCommand(OperationalMode.NOMINAL))]


def finished(software):
    return lambda outputs: software.routeExecutor.isRouteComplete


if __name__ == "__main__":
    fullPlan = planRoute(area, subAreas, startPoint)
    software, simulator = buildScenario(fullPlan, 1.0)
    result = runSimulation(software, simulator, 12 * secondsPerHour, 10.0,
                           scheduledCommands=commandsToStart(fullPlan), stopCondition=finished(software))
    printResult("1. normal mission (6 farms, full battery)", software, simulator, result)

    shortPlan = planRoute(area, subAreas, startPoint, budget=Budget(maxDistanceMeters=15000))
    software, simulator = buildScenario(shortPlan, 0.55)
    result = runSimulation(
        software, simulator, 24 * secondsPerHour, 30.0, scheduledCommands=commandsToStart(shortPlan),
        scheduledActions=[(2.5 * secondsPerHour, lambda vessel: setattr(vessel, "solarPowerWatts", 150.0))],
        stopCondition=finished(software))
    printResult("2. low battery pause, solar power starts after 2.5 h (starts at 55%)",
                software, simulator, result)

    software, simulator = buildScenario(fullPlan, 1.0)
    faultTime = 2 * secondsPerHour
    commands = commandsToStart(fullPlan) + [
        (faultTime + 600.0, ModeChangeCommand(OperationalMode.NOMINAL)),
        (faultTime + 1200.0, ModeChangeCommand(OperationalMode.STANDBY))]
    result = runSimulation(
        software, simulator, faultTime + 2000.0, 10.0, scheduledCommands=commands,
        scheduledActions=[(faultTime, lambda vessel: setattr(vessel, "thrusterIsOperational", False))])
    printResult("3. thruster fault after 2 h, then two telecommands", software, simulator, result)
