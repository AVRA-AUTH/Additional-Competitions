"""Zones = what the user draws on the map. Pure functions, no UI imports, so the planner can use them too.

Zone: {"bbox": [lon_min, lat_min, lon_max, lat_max], "weight": int, "role": "background" | "priority"}
Order = draw order. The first zone is the background area of interest; the rest are stacked on top.
Importance of a point = sum of the weights of all zones that contain it."""
from avra import config


def zones_from_geojson(gj):
    """dash-leaflet EditControl geojson -> list of zones (rectangles only)."""
    zones = []
    for f in (gj or {}).get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") != "Polygon" or not g.get("coordinates"):
            continue
        pts = g["coordinates"][0]
        lons, lats = [p[0] for p in pts], [p[1] for p in pts]
        first = not zones
        zones.append({"bbox": [min(lons), min(lats), max(lons), max(lats)],
                      "weight": config.BG_WEIGHT if first else config.STACK_WEIGHT,
                      "role": "background" if first else "priority"})
    return zones


def zone_bounds(z):
    """Zone -> Leaflet bounds [[south, west], [north, east]]."""
    w, s, e, n = z["bbox"]
    return [[s, w], [n, e]]


def inside_grid(z, grid_bounds):
    """True if the whole zone lies inside the Copernicus grid ([[s, w], [n, e]])."""
    w, s, e, n = z["bbox"]
    (gs, gw), (gn, ge) = grid_bounds
    return s >= gs and n <= gn and w >= gw and e <= ge
