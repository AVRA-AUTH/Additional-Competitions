import json
from datetime import date
from functools import lru_cache

import dash_leaflet as dl
import numpy as np
from dash import Dash, dcc, html, ctx, no_update, Input, Output, State

from avra import config
from avra.copernicus.client import fetch, days, field_at, sample
from avra.ui import icons
from avra.ui.render import field_to_png, colourbar_uri, grid_bounds, layer_scale, globe
from avra.zones import zones_from_geojson, zone_bounds, inside_grid
from avra.integration.api import api_blueprint
from avra.integration.mission import run_mission

# ---- data (read once from the local cache; never touch the network from a callback) -------------
ds = fetch()
VARS = [v for v in config.VARIABLES if v in ds.data_vars]
DAYS = days(ds)
TODAY = np.datetime64(date.today())
I0 = int(np.abs((DAYS - TODAY).astype(int)).argmin())          # day closest to today
SCALES = {v: layer_scale(ds, v) for v in VARS}
BOUNDS = grid_bounds(field_at(ds, VARS[0], I0))
CENTER = list(config.CENTER) if config.CENTER else [(BOUNDS[0][0] + BOUNDS[1][0]) / 2,
                                                    (BOUNDS[0][1] + BOUNDS[1][1]) / 2]
CELL_KM = abs(float(ds.latitude[1] - ds.latitude[0])) * 111.0


def label(v):
    return config.LAYER_LABELS.get(v, v)


def latlng(click):
    """Leaflet click -> (lat, lon). Accepts {"lat","lng"} and [lat, lng] (depends on dash-leaflet version)."""
    ll = click["latlng"]
    return (ll["lat"], ll["lng"]) if isinstance(ll, dict) else (ll[0], ll[1])


@lru_cache(maxsize=48)
def overlay(var, i):
    return field_to_png(field_at(ds, var, i), *SCALES[var])[0]


@lru_cache(maxsize=16)
def cbar(var):
    return colourbar_uri(*SCALES[var])


def rel_day(i):
    d = int((DAYS[i] - TODAY).astype(int))
    return "today" if d == 0 else (f"forecast +{d} d" if d > 0 else f"{-d} d ago")


def status_rows(var, i):
    rows = [html.Div(f"{config.ACTIVE} | cell about {CELL_KM:.1f} km"),
            html.Div(f"{label(var)}, {DAYS[i]} ({rel_day(i)})"),
            html.Div(f"Cache: {DAYS[0]} to {DAYS[-1]}", className="small")]
    if globe is not None:
        rows.append(html.Div("Coast: cells touching land are filled from the nearest sea cell (display only).",
                             className="small"))
    if DAYS[-1] < TODAY:
        rows.append(html.Div("Cache ends before today. Refresh: python -m scripts.fetch_chl --refresh",
                             className="warn"))
    return rows


def mark_style(i):
    d = int((DAYS[i] - TODAY).astype(int))
    if d == 0:
        return {"color": "#0b3d91", "fontWeight": "700"}
    return {"color": "#1a7f5a"} if d > 0 else {"color": "#6b7a90"}


MARKS = {i: {"label": f"{str(DAYS[i])[8:]}/{str(DAYS[i])[5:7]}", "style": mark_style(i)}
         for i in range(len(DAYS))}

SPEED = [("fast", "Fast"), ("medium", "Medium"), ("slow", "Slow / fine")]
MODES = [("nominal", "Nominal"), ("standby", "Standby"), ("safe", "Safe")]
CARDS_A = [("battery", "% battery left"), ("autonomy", "Remaining time for autonomous movement"),
           ("mode", "Mode (nominal / standby / safe)"), ("comms", "Communication with boat")]
CARDS_B = [("route", "% of completed route for monitoring"), ("loop", "Total time for 1 monitoring circle")]


def segmented(prefix, text, options, default):
    """Rounded 'selection' control from the mockup: a pill with one button per choice."""
    return html.Div(className="pill", children=[
        html.Span(text, className="pilllbl"),
        *[html.Button(t, id=f"{prefix}-{v}", n_clicks=0, className="seg on" if v == default else "seg")
          for v, t in options]])


def card(cid, text, extra=""):
    return html.Div([html.Span(text), html.B("-", id=f"card-{cid}")], className=f"card {extra}".strip())


# ---- layout ---------------------------------------------------------------------------------------
app = Dash(__name__, assets_folder=str(config.ROOT / "assets"))
app.server.register_blueprint(api_blueprint)
app.layout = html.Div(className="shell", children=[
    dcc.Store(id="pin_mode", data=False), dcc.Store(id="start", data=None), dcc.Store(id="run", data=False),
    dcc.Store(id="zones", data=[]),
    dcc.Store(id="last_mission", data=None),
    dcc.Store(id="speed", data="medium"), dcc.Store(id="mode_cmd", data="nominal"),

    html.Div(className="toolbar", children=[
        html.Button(html.Img(src=icons.FIT), id="btn-fit", n_clicks=0, className="tool",
                    title="Zoom to the area of interest"),
        html.Button(html.Img(src=icons.PIN), id="btn-pin", n_clicks=0, className="tool",
                    title="Set the start position, then click the map"),
        html.Button(html.Img(src=icons.ZOOM), id="btn-zoom", n_clicks=0, className="tool",
                    title="Zoom in on the selected position (or the start)"),
        html.Button(html.Img(src=icons.PLAY, id="btn-run-img"), id="btn-run", n_clicks=0, className="tool",
                    title="Start / stop"),
        segmented("speed", "Monitoring speed / accuracy", SPEED, "medium"),
        segmented("mode", "Change to mode", MODES, "nominal"),
        html.Div(id="hint", className="hint"),
    ]),

    html.Div(className="body", children=[
        html.Div(className="cbar", children=html.Img(id="cbar", src=cbar(VARS[0]))),
        html.Div(className="mapwrap", children=[
            dl.Map(id="map", center=CENTER, zoom=config.ZOOM, style={"height": "100%"}, children=[
                dl.TileLayer(),
                dl.ImageOverlay(id="overlay", url=overlay(VARS[0], I0), bounds=BOUNDS, opacity=0.85),
                dl.LayerGroup(id="sel"),
                dl.FeatureGroup([dl.EditControl(id="edit", draw={
                    "rectangle": {"shapeOptions": {"color": "#111", "weight": 2, "dashArray": "6 5",
                                                   "fill": False}},
                    "polygon": False, "polyline": False,
                    "circle": False, "marker": False, "circlemarker": False})]),
                dl.LayerGroup(id="zone_layer"),
                dl.LayerGroup(id="start_layer"),
                dl.LayerGroup(id="route_layer"),
                dl.LayerGroup(id="samples_layer"),
                dl.LayerGroup(id="divergence_layer"),
            ]),
            html.Div(className="floatwrap", children=[
                html.Div(id="ship_box", className="floatbox"),
                html.Div(id="sel_box", className="floatbox", children="Selected position: click the map"),
            ]),
        ]),
        html.Div(className="side", children=[
            *[card(c, t) for c, t in CARDS_A],
            *[card(c, t, "gap" if k == 0 else "") for k, (c, t) in enumerate(CARDS_B)],
        ]),
    ]),

    html.Div(className="bottombar", children=[
        html.Div([html.Div("Copernicus layer", className="lbl"),
                  dcc.Dropdown(id="layer", options=[{"label": label(v), "value": v} for v in VARS],
                               value=VARS[0], clearable=False)], style={"width": "250px"}),
        html.Div([html.Div("Day (grey past, blue today, green forecast)", className="lbl"),
                  dcc.Slider(id="day", min=0, max=len(DAYS) - 1, step=1, value=I0,
                             marks=MARKS, included=False)], style={"flex": "1"}),
        html.Div(id="status", className="status"),
    ]),
    html.Div(id="zone_note", className="warn2"),
    html.Div(id="mission_summary", className="mission-summary"),
    html.Details(className="dev", children=[html.Summary("Zones sent to the planner"),
                                            html.Pre(id="zones_json")]),
])


# ---- Copernicus layer: overlay, colourbar, status -----------------------------------------------
@app.callback(Output("overlay", "url"), Output("cbar", "src"), Output("status", "children"),
              Input("layer", "value"), Input("day", "value"))
def show(var, i):
    return overlay(var, i), cbar(var), status_rows(var, i)


# ---- selected position (click readout) -------------------------------------------------------------
@app.callback(Output("sel", "children"), Output("sel_box", "children"),
              Input("map", "clickData"), Input("layer", "value"), Input("day", "value"))
def pick(click, var, i):
    if not click:
        return [], "Selected position: click the map"
    lat, lon = latlng(click)
    da = field_at(ds, var, i)
    s = sample(da, lat, lon)
    if s.status == "land":
        s = sample(da, lat, lon, fallback_cells=2)         # coast strip: show nearest sea cell, say so
    val = "-" if s.status in ("land", "outside") else f"{s.value:.3f} mg/m³"
    rows = [html.B("Selected position"),
            html.Div(f"X: {lon:.4f}   Y: {lat:.4f}   value: {val}"),
            html.Div(f"{label(var)}, {DAYS[i]} ({rel_day(i)})", className="small")]
    if s.status == "fallback":
        rows.append(html.Div(f"Land-masked cell: nearest sea cell, {s.dist_km:.1f} km away. Not a local value.",
                             className="warn"))
    elif s.status == "land":
        rows.append(html.Div("No data here (land mask, no sea cell within 2 cells)", className="warn"))
    elif s.status == "outside":
        rows.append(html.Div("Outside the Copernicus grid, no data", className="warn"))
    outline = [dl.Rectangle(bounds=s.bounds, color="white", weight=2, fill=False)] if s.bounds else []
    return outline, rows


# ---- start position (pin button, then click) -------------------------------------------------------
@app.callback(Output("pin_mode", "data"), Output("start", "data"), Output("btn-pin", "className"),
              Input("btn-pin", "n_clicks"), Input("map", "clickData"),
              State("pin_mode", "data"), State("start", "data"), prevent_initial_call=True)
def pin(_n, click, mode, start):
    if ctx.triggered_id == "btn-pin":
        mode = not mode
        return mode, start, "tool on" if mode else "tool"
    if mode and click:
        lat, lon = latlng(click)
        return False, {"lat": lat, "lon": lon}, "tool"
    return no_update, no_update, no_update


@app.callback(Output("start_layer", "children"), Input("start", "data"))
def draw_start(start):
    if not start:
        return []
    return [dl.CircleMarker(center=[start["lat"], start["lon"]], radius=8, color="#000", weight=2,
                            fillColor="#ffd54a", fillOpacity=1, interactive=False,
                            children=dl.Tooltip("Start", permanent=True, direction="top"))]


@app.callback(Output("ship_box", "children"), Input("start", "data"), Input("layer", "value"),
              Input("day", "value"), Input("last_mission", "data"))
def ship_box(start, var, i, last_mission):
    rows = [html.B("Current ship position"), html.Div("X: -   Y: -   value: -  (run a mission to connect the simulator)")]
    if last_mission and last_mission.get("final_position"):
        position = last_mission["final_position"]
        rows = [html.B("Current ship position"),
                html.Div(f"X: {position['longitude']:.4f}   Y: {position['latitude']:.4f}  (last simulation)")]
    if start:
        s = sample(field_at(ds, var, i), start["lat"], start["lon"], fallback_cells=2)
        v = "-" if s.status in ("land", "outside") else f"{s.value:.3f}"
        rows.append(html.Div(f"Planned start  X: {start['lon']:.4f}   Y: {start['lat']:.4f}   value: {v}",
                             className="small"))
        if s.status in ("land", "outside"):
            rows.append(html.Div("Start has no Copernicus value here", className="warn"))
    return rows


# ---- zoom buttons (fit / zoom in) ---------------------------------------------------------------------
@app.callback(Output("map", "viewport"), Input("btn-fit", "n_clicks"), Input("btn-zoom", "n_clicks"),
              State("zones", "data"), State("start", "data"), State("map", "clickData"),
              prevent_initial_call=True)
def zoom(_a, _b, zones, start, click):
    if ctx.triggered_id == "btn-fit":
        b = zone_bounds(zones[0]) if zones else BOUNDS
        return {"bounds": b, "transition": "flyToBounds"}
    if click:
        lat, lon = latlng(click)
    elif start:
        lat, lon = start["lat"], start["lon"]
    else:
        return no_update
    return {"center": [lat, lon], "zoom": 10, "transition": "flyTo"}


# ---- end-to-end mission: UI zones -> planner -> onboard simulator -> validator ---------------------------
def _mission_route_layer(plan_history):
    layers = []
    for index, item in enumerate(plan_history or []):
        plan = item.get("plan", {})
        positions = [[wp["latitude"], wp["longitude"]] for wp in plan.get("waypoints", [])]
        if len(positions) >= 2:
            layers.append(dl.Polyline(positions=positions, color="#00d5ff" if index == 0 else "#ffbf47",
                                      weight=4 if index == 0 else 3, opacity=0.9,
                                      dashArray=None if index == 0 else "7 6",
                                      children=dl.Tooltip("Initial planned route" if index == 0 else
                                                          f"Replan: {item.get('reason', 'updated route')}")))
    return layers


def _mission_measurement_layer(run_result):
    import csv
    rows = []
    path = run_result.get("output_dir")
    if path:
        try:
            with open(f"{path}/crosscheck_samples.csv", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError:
            rows = []
    if not rows:
        return []
    stride = max(1, len(rows) // 220)
    layers = []
    for row in rows[::stride]:
        try:
            lat, lon = float(row["latitude"]), float(row["longitude"])
        except (TypeError, ValueError, KeyError):
            continue
        layers.append(dl.CircleMarker(center=[lat, lon], radius=2.5, color="#d2f5ff",
                                      fillColor="#00bfe8", fillOpacity=0.8, weight=1,
                                      children=dl.Tooltip("ASV measurement")))
    return layers


def _mission_divergence_layer(run_result):
    layers = []
    for cell in (run_result.get("validation") or {}).get("cell_results", []):
        if not cell.get("flagged") or not cell.get("cell_bounds"):
            continue
        bounds = cell["cell_bounds"]
        lat = (float(bounds[0][0]) + float(bounds[1][0])) / 2.0
        lon = (float(bounds[0][1]) + float(bounds[1][1])) / 2.0
        bias = float(cell.get("bias_sensor_minus_copernicus_mg_m3", 0.0))
        layers.append(dl.CircleMarker(center=[lat, lon], radius=8, color="#ff3030",
                                      fillColor="#ff3030", fillOpacity=0.85, weight=2,
                                      children=dl.Tooltip(
                                          f"Divergence: bias {bias:+.3f} mg/m³; n={cell.get('count', 0)}")))
    return layers


@app.callback(
    Output("run", "data"), Output("btn-run-img", "src"),
    Output("route_layer", "children"), Output("samples_layer", "children"),
    Output("divergence_layer", "children"), Output("mission_summary", "children"),
    Output("last_mission", "data"), Output("card-battery", "children"),
    Output("card-autonomy", "children"), Output("card-mode", "children"),
    Output("card-comms", "children"), Output("card-route", "children"),
    Output("card-loop", "children"),
    Input("btn-run", "n_clicks"), State("start", "data"), State("zones", "data"),
    State("layer", "value"), State("speed", "data"), prevent_initial_call=True)
def run(_n, start, zones, variable, speed):
    empty = (False, icons.PLAY, [], [], [], html.Div(), None, "-", "-", "-", "-", "-", "-")
    if not start:
        return (*empty[:5], html.Div("Set a launch position first: click the pin tool, then click the map.", className="warn"),
                {"status": "not_started", "error": "No start position was set."}, *empty[7:])
    spacing = {"fast": 40.0, "medium": 20.0, "slow": 5.0}.get(speed, 20.0)
    payload = {
        "zones": zones or [],
        "start": {"lat": start.get("lat"), "lon": start.get("lon")},
        "variable": variable,
        "budget_meters": 30000.0,
        "buffer_meters": 50.0,
        "sample_spacing_meters": spacing,
        "coast_rule_km": 3.0,
        "enforce_coast_rule": True,
        "divergence_threshold_mg_m3": 0.15,
    }
    try:
        result = run_mission(payload, output_root=config.ROOT / "runs", dataset=ds)
    except Exception as error:
        error_result = {"status": "error", "error": str(error)}
        return (False, icons.PLAY, [], [], [],
                html.Div([html.B("Mission could not start. "), str(error)], className="warn"),
                error_result, "-", "-", "-", "-", "-", "-")

    plan_history = result.get("plan_history", [])
    if not plan_history and result.get("plan"):
        plan_history = [{"reason": "initial", "plan": result["plan"]}]
    validation = result.get("validation", {})
    cells_flagged = validation.get("cells_flagged", 0)
    cells_compared = validation.get("cells_compared", 0)
    global_bias = validation.get("global_bias_sensor_minus_copernicus_mg_m3")
    bias_text = "n/a" if global_bias is None else f"{global_bias:+.3f} mg/m³"
    message = html.Div([
        html.B(f"Mission {result.get('status', 'finished')}. "),
        html.Span(f"{result.get('sample_count', 0)} samples; {cells_flagged}/{cells_compared} Copernicus cells flagged; "),
        html.Span(f"global sensor-minus-product bias {bias_text}."),
        html.Div(f"Replans: {len(result.get('replans', []))} · stop: {result.get('stop_reason', result.get('message', 'unknown'))}"),
        html.Div(f"Artifacts: {result.get('output_dir', 'not written')}", className="small"),
    ])
    route_layers = _mission_route_layer(plan_history)
    sample_layers = _mission_measurement_layer(result)
    divergence_layers = _mission_divergence_layer(result)
    total = max(1, len((plan_history[0].get("plan", {}).get("covered_subareas", [])) if plan_history else []))
    progress = f"{100.0 * len(result.get('completed_subareas', [])) / total:.0f}%"
    remaining_h = max(0.0, float(result.get("remaining_budget_m", 0.0)) / 1.028888 / 3600.0)
    first_plan = (plan_history[0].get("plan", {}) if plan_history else {})
    loop_h = float(first_plan.get("estimated_duration_s", 0.0)) / 3600.0
    battery = result.get("final_battery_fraction")
    battery_text = "-" if battery is None else f"{100 * float(battery):.0f}%"
    status = result.get("status", "unknown")
    return (False, icons.PLAY, route_layers, sample_layers, divergence_layers, message, result,
            battery_text, f"{remaining_h:.1f} h", result.get("final_mode", status),
            "connected (simulated)", progress, f"{loop_h:.1f} h")


@app.callback(Output("hint", "children"), Input("pin_mode", "data"), Input("run", "data"),
              Input("last_mission", "data"))
def hint(pin_mode, running, last_mission):
    if pin_mode:
        return "Click the map to place the start position."
    if running:
        return "Mission running."
    if last_mission and last_mission.get("status") == "complete":
        return "Mission complete: cyan circles are samples; red circles identify Copernicus cells with divergence."
    if last_mission and last_mission.get("error"):
        return "Mission was not started; check the message below and make sure the coast mask/dependencies are installed."
    return "Draw one background rectangle first, then one or more monitoring rectangles. Pin a start position, then press Start."


# ---- selection pills (speed / mode) ---------------------------------------------------------------------
def wire_segmented(prefix, options, store=None):
    ids = [f"{prefix}-{v}" for v, _ in options]

    @app.callback(Output(store or prefix, "data"), *[Output(i, "className") for i in ids],
                  *[Input(i, "n_clicks") for i in ids], prevent_initial_call=True)
    def _pick(*_):
        chosen = ctx.triggered_id.split("-", 1)[1]
        return (chosen, *["seg on" if v == chosen else "seg" for v, _ in options])


wire_segmented("speed", SPEED)
wire_segmented("mode", MODES, store="mode_cmd")


@app.callback(Output("card-mode", "children"), Input("mode_cmd", "data"))
def mode_card(m):
    return f"{m} (commanded)"


# ---- zones (rectangles drawn by the user) -----------------------------------------------------------------
@app.callback(Output("zones", "data"), Output("zone_layer", "children"), Output("zones_json", "children"),
              Output("zone_note", "children"), Input("edit", "geojson"))
def zones_cb(gj):
    zs = zones_from_geojson(gj)
    layers, notes = [], []
    for k, z in enumerate(zs):
        b = zone_bounds(z)
        cy, cx = (b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2
        if k > 0:       # priority areas get a white veil: overlaps get brighter = more important
            layers.append(dl.Rectangle(bounds=b, weight=0, fillColor="white", fillOpacity=0.3,
                                       interactive=False))
        layers.append(dl.CircleMarker(center=[cy, cx], radius=1, color="#111", interactive=False,
                                      children=dl.Tooltip("Area of interest" if k == 0 else f"+{z['weight']}",
                                                          permanent=True, direction="center")))
        if not inside_grid(z, BOUNDS):
            notes.append(f"Zone {k + 1} is partly outside the Copernicus grid. ")
    return zs, layers, json.dumps(zs, indent=1), "".join(notes)


if __name__ == "__main__":
    app.run(debug=False)
