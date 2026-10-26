"""
@file plotDemoRoutePlanner.py
@brief Draws the demo route so it can be checked by eye. Needs matplotlib.
@details Run with: python3 plotDemoRoutePlanner.py  (writes demoRoute.png)
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as pyplot

from demoRoutePlanner import area, frame, startPoint, subAreas
from routePlanner import GeoPoint, planRoute

plan = planRoute(area, subAreas, startPoint)


def toPlanar(latitude: float, longitude: float):
    return frame.toPlanar(GeoPoint(latitude, longitude))


figure, axes = pyplot.subplots(figsize=(8, 8))
areaPlanar = [frame.toPlanar(corner) for corner in area]
axes.fill([point[0] for point in areaPlanar], [point[1] for point in areaPlanar],
          facecolor="none", edgecolor="gray", linestyle="--", label="area of interest")
for subArea in subAreas:
    corners = [frame.toPlanar(corner) for corner in subArea.corners]
    axes.fill([point[0] for point in corners], [point[1] for point in corners],
              color="tab:orange", alpha=0.6)
    axes.annotate(subArea.identifier, (sum(point[0] for point in corners) / 4,
                                       sum(point[1] for point in corners) / 4), ha="center")
for previous, current in zip(plan.waypoints, plan.waypoints[1:]):
    first = toPlanar(previous.latitude, previous.longitude)
    second = toPlanar(current.latitude, current.longitude)
    axes.plot([first[0], second[0]], [first[1], second[1]],
              color="tab:blue" if current.isSamplingLeg else "tab:green",
              linewidth=2 if current.isSamplingLeg else 1.2)
startPlanar = toPlanar(plan.waypoints[0].latitude, plan.waypoints[0].longitude)
axes.plot(*startPlanar, "k*", markersize=14, label="start")
axes.plot([], [], color="tab:blue", label="sampling ring")
axes.plot([], [], color="tab:green", label="transit")
axes.set_aspect("equal")
axes.set_xlabel("east (m)")
axes.set_ylabel("north (m)")
axes.set_title(f"{plan.totalDistanceMeters / 1000:.1f} km, {plan.status.value}")
axes.legend(loc="lower right")
figure.savefig("demoRoute.png", dpi=110)
print("wrote demoRoute.png")
