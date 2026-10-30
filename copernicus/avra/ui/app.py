import json
from datetime import date
from functools import lru_cache

import dash_leaflet as dl
import numpy as np
from dash import Dash, dcc, html, ctx, no_update, Input, Output, State, ALL

from avra import config
from avra.copernicus.client import fetch, days, field_at, sample
from avra.ui import icons
from avra.ui.render import (
    field_to_png, colourbar_uri, grid_bounds, layer_scale,
    globe, coastline_segments
)
from avra.zones import zones_from_geojson, zone_bounds, inside_grid
from avra.integration.api import api_blueprint
from avra.integration.mission import run_mission


# ---- data (read once from the local cache; callbacks do not fetch) -------------------------------
ds = fetch()
VARS = [v for v in config.VARIABLES if v in ds.data_vars]
if not VARS:
    raise RuntimeError("No configured Copernicus variables were found in the cached dataset.")

DAYS = days(ds)
if len(DAYS) == 0:
    raise RuntimeError("The Copernicus cache contains no dates.")

TODAY = np.datetime64(date.today())
I0 = int(np.abs((DAYS - TODAY).astype(int)).argmin())
SCALES = {v: layer_scale(ds, v) for v in VARS}
BOUNDS = grid_bounds(field_at(ds, VARS[0], I0))

CENTER = (
    list(config.CENTER)
    if config.CENTER
    else [
        (BOUNDS[0][0] + BOUNDS[1][0]) / 2,
        (BOUNDS[0][1] + BOUNDS[1][1]) / 2
    ]
)

CELL_KM = abs(float(ds.latitude[1] - ds.latitude[0])) * 111.0

MAX_COAST_KM = 5.0
COAST_FALLBACK_CELLS = max(
    2, int(np.ceil(MAX_COAST_KM / CELL_KM)) + 1
)

# Spatial coverage: cells with at least one valid Copernicus observation.
# This may retain a singleton depth dimension; render.py handles it.
COVERAGE = ds[VARS[0]].notnull().any(dim="time")

# Coastline and offshore boundary, restricted to Copernicus-covered sea.
COASTLINE, COAST_5KM = coastline_segments(
    BOUNDS, MAX_COAST_KM, COVERAGE
)


def label(v):
    return config.LAYER_LABELS.get(v, v)


def latlng(click):
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
    rows = [
        html.Div(f"{config.ACTIVE} | cell about {CELL_KM:.1f} km"),
        html.Div(f"{label(var)}, {DAYS[i]} ({rel_day(i)})"),
        html.Div(f"Cache: {DAYS[0]} to {DAYS[-1]}", className="small")
    ]
    if globe is not None:
        rows.append(html.Div(
            "Coast: land-masked cells near sea are filled for display only.",
            className="small"
        ))
    if DAYS[-1] < TODAY:
        rows.append(html.Div(
            "Cache ends before today. Refresh: python -m scripts.fetch_chl --refresh",
            className="warn"
        ))
    return rows


def mark_style(i):
    d = int((DAYS[i] - TODAY).astype(int))
    if d == 0:
        return {"color": "#0b3d91", "fontWeight": "700"}
    return {"color": "#1a7f5a"} if d > 0 else {"color": "#6b7a90"}


MARKS = {
    i: {
        "label": f"{str(DAYS[i])[8:]}/{str(DAYS[i])[5:7]}",
        "style": mark_style(i)
    }
    for i in range(len(DAYS))
}

SPEED = [("fast", "Fast"), ("medium", "Medium"), ("slow", "Slow / fine")]
MODES = [("nominal", "Nominal"), ("standby", "Standby"), ("safe", "Safe")]
CARDS_A = [
    ("battery", "% battery left"),
    ("autonomy", "Remaining time for autonomous movement"),
    ("mode", "Mode (nominal / standby / safe)"),
    ("comms", "Communication with boat")
]
CARDS_B = [
    ("route", "% of completed route for monitoring"),
    ("loop", "Total time for 1 monitoring circle")
]


def segmented(prefix, text, options, default):
    return html.Div(className="pill", children=[
        html.Span(text, className="pilllbl"),
        *[
            html.Button(
                t, id=f"{prefix}-{v}", n_clicks=0,
                className="seg on" if v == default else "seg"
            )
            for v, t in options
        ]
    ])


def card(cid, text, extra=""):
    return html.Div(
        [html.Span(text), html.B("-", id=f"card-{cid}")],
        className=f"card {extra}".strip()
    )


# ---- UI-side checks ------------------------------------------------------------------------------
def coast_problem(lat, lon):
    try:
        da = field_at(ds, VARS[0], I0)
        s = sample(da, lat, lon, fallback_cells=COAST_FALLBACK_CELLS)
    except Exception:
        return None

    if s.status == "outside":
        return "is outside the Copernicus grid"
    if s.status == "land":
        return f"is more than {MAX_COAST_KM:g} km from the coast"
    if s.status == "fallback" and getattr(s, "dist_km", 0.0) > MAX_COAST_KM:
        return f"is {s.dist_km:.1f} km from the coast (max {MAX_COAST_KM:g} km)"
    return None


def zone_coast_problem(z):
    try:
        (lat0, lon0), (lat1, lon1) = zone_bounds(z)
        pts = [
            (lat0, lon0), (lat1, lon0), (lat0, lon1), (lat1, lon1),
            ((lat0 + lat1) / 2, (lon0 + lon1) / 2)
        ]
        for lat, lon in pts:
            problem = coast_problem(lat, lon)
            if problem:
                return problem
    except Exception:
        return None
    return None


def zone_outside(inner_z, outer_z, eps=1e-4):
    """True if inner_z is not fully inside outer_z (bounding boxes)."""
    try:
        (i0, i1), (o0, o1) = zone_bounds(inner_z), zone_bounds(outer_z)
        return not (
            i0[0] >= o0[0] - eps and i0[1] >= o0[1] - eps
            and i1[0] <= o1[0] + eps and i1[1] <= o1[1] + eps
        )
    except Exception:
        return False


# ---- tutorial / instructions ---------------------------------------------------------------------
HELP_HIDE = {"display": "none"}
HELP_SHOW = {
    "display": "flex", "position": "fixed", "inset": "0", "zIndex": "10000",
    "background": "rgba(0,0,0,0.55)", "alignItems": "center",
    "justifyContent": "center"
}
HELP_BOX = {
    "background": "#fff", "color": "#1b2430", "maxWidth": "680px", "width": "92%",
    "maxHeight": "85vh", "overflowY": "auto", "borderRadius": "12px",
    "padding": "18px 24px", "boxShadow": "0 10px 40px rgba(0,0,0,0.4)",
    "fontSize": "14px", "lineHeight": "1.5"
}

HELP = {
    "en": {
        "title": "How to use this tool",
        "steps": [
            ("Look at the data. ", "Use the layer menu and the day slider at the bottom to see the Copernicus data (grey = past, blue = today, green = forecast). Click the map to read a value."),
            ("Draw the area of interest. ", "Use the rectangle tool on the map and draw ONE big rectangle first. This first rectangle is the area of interest."),
            ("Draw at least one monitoring rectangle. ", "Draw one or more extra rectangles INSIDE the first one. Rectangles outside it are shown in red and cannot be used for a mission until you delete them."),
            ("Stay within 3 km of the coast. ", f"Every area and the start position must be within {MAX_COAST_KM:g} km of the coast."),
            ("Set the start position. ", "Press the pin button, then click the map."),
            ("Choose speed / accuracy. ", "Fast = sparse samples, Slow / fine = dense samples."),
            ("Start the mission. ", "Press the play button."),
            ("Read the result. ", "Blue line = initial route, orange dashed = replans, cyan dots = measurements, red circles = cells where the sensor disagrees with Copernicus."),
            ("Delete one area. ", "Press the × next to that area in the zone list. Other areas stay.")
        ],
        "close": "Close"
    },
    "el": {
        "title": "Οδηγίες χρήσης",
        "steps": [
            ("Δείτε τα δεδομένα. ", "Με το μενού στρώματος και τη μπάρα ημερών κάτω βλέπετε τα δεδομένα Copernicus. Πατήστε στον χάρτη για να δείτε την τιμή."),
            ("Σχεδιάστε την περιοχή ενδιαφέροντος. ", "Με το εργαλείο ορθογωνίου, σχεδιάστε πρώτα ΕΝΑ μεγάλο ορθογώνιο."),
            ("Σχεδιάστε τουλάχιστον ένα ορθογώνιο παρακολούθησης. ", "Σχεδιάστε ένα ή περισσότερα ορθογώνια ΜΕΣΑ στο πρώτο. Όσα είναι έξω εμφανίζονται με κόκκινο και δεν μπορούν να χρησιμοποιηθούν σε αποστολή μέχρι να τα διαγράψετε."),
            ("Μείνετε εντός 3 km από την ακτή. ", f"Κάθε περιοχή και η αφετηρία πρέπει να είναι έως {MAX_COAST_KM:g} km από την ακτή."),
            ("Ορίστε την αφετηρία. ", "Πατήστε το κουμπί με την καρφίτσα και μετά κάντε κλικ στον χάρτη."),
            ("Επιλέξτε ταχύτητα / ακρίβεια. ", "Fast = αραιά δείγματα, Slow / fine = πυκνά δείγματα."),
            ("Ξεκινήστε την αποστολή. ", "Πατήστε το κουμπί play."),
            ("Διαβάστε το αποτέλεσμα. ", "Μπλε γραμμή = αρχική διαδρομή, πορτοκαλί διακεκομμένη = επανασχεδιασμός, γαλάζιες κουκκίδες = μετρήσεις, κόκκινοι κύκλοι = κελιά με απόκλιση."),
            ("Διαγραφή μίας περιοχής. ", "Πατήστε το × δίπλα στην περιοχή στη λίστα. Οι υπόλοιπες μένουν.")
        ],
        "close": "Κλείσιμο"
    }
}


def help_body(lang):
    h = HELP[lang]
    return [
        html.H3(h["title"], style={"marginTop": "0"}),
        html.Ol([
            html.Li([html.B(a), b], style={"marginBottom": "6px"})
            for a, b in h["steps"]
        ])
    ]


# ---- layout --------------------------------------------------------------------------------------
app = Dash(__name__, assets_folder=str(config.ROOT / "assets"))
app.server.register_blueprint(api_blueprint)

app.layout = html.Div(className="shell", children=[
    dcc.Store(id="pin_mode", data=False),
    dcc.Store(id="start", data=None),
    dcc.Store(id="run", data=False),
    dcc.Store(id="zones", data=[]),
    dcc.Store(id="hidden_zones", data=[]),
    dcc.Store(id="last_mission", data=None),
    dcc.Store(id="speed", data="medium"),
    dcc.Store(id="mode_cmd", data="nominal"),

    html.Div(className="toolbar", children=[
        html.Button(html.Img(src=icons.FIT), id="btn-fit", n_clicks=0,
                    className="tool", title="Zoom to the area of interest"),
        html.Button(html.Img(src=icons.PIN), id="btn-pin", n_clicks=0,
                    className="tool", title="Set the start position, then click the map"),
        html.Button(html.Img(src=icons.ZOOM), id="btn-zoom", n_clicks=0,
                    className="tool", title="Zoom in on the selected position (or the start)"),
        html.Button(html.Img(src=icons.PLAY, id="btn-run-img"), id="btn-run",
                    n_clicks=0, className="tool", title="Start / stop"),
        # The button owns the ID; its image deliberately has no duplicate ID.
        html.Button(html.Img(src=icons.INFO), id="btn-help", n_clicks=0,
                    className="tool", title="Information / Help"),
        segmented("speed", "Monitoring speed / accuracy", SPEED, "medium"),
        segmented("mode", "Change to mode", MODES, "nominal"),
        html.Div(id="hint", className="hint")
    ]),

    html.Div(id="pin_note", className="warn2"),

    html.Div(className="body", children=[
        html.Div(className="cbar", children=html.Img(id="cbar", src=cbar(VARS[0]))),
        html.Div(className="mapwrap", children=[
            dl.Map(id="map", center=CENTER, zoom=config.ZOOM,
                   style={"height": "100%"}, children=[
                dl.TileLayer(),
                dl.ImageOverlay(
                    id="overlay", url=overlay(VARS[0], I0),
                    bounds=BOUNDS, opacity=0.85
                ),
                dl.LayerGroup(
                    id="coastline_layer",
                    children=[
                        dl.Polyline(
                            positions=segment, color="#000000",
                            weight=2, opacity=1, interactive=False
                        )
                        for segment in COASTLINE
                    ]
                ),
                dl.LayerGroup(
                    id="coast_buffer_layer",
                    children=[
                        dl.Polyline(
                            positions=segment, color="#ff0000",
                            weight=2, opacity=0.95, dashArray="7 5",
                            interactive=False
                        )
                        for segment in COAST_5KM
                    ]
                ),
                dl.LayerGroup(id="sel"),
                dl.FeatureGroup([dl.EditControl(
                    id="edit",
                    draw={
                        "rectangle": {
                            "shapeOptions": {
                                "color": "#111", "weight": 2,
                                "dashArray": "6 5", "fill": False
                            }
                        },
                        "polygon": False, "polyline": False,
                        "circle": False, "marker": False,
                        "circlemarker": False
                    }
                )]),
                dl.LayerGroup(id="zone_layer"),
                dl.LayerGroup(id="start_layer"),
                dl.LayerGroup(id="route_layer"),
                dl.LayerGroup(id="samples_layer"),
                dl.LayerGroup(id="divergence_layer")
            ]),
            html.Div(className="floatwrap", children=[
                html.Div(id="ship_box", className="floatbox"),
                html.Div(
                    id="sel_box", className="floatbox",
                    children="Selected position: click the map"
                )
            ])
        ]),
        html.Div(className="side", children=[
            *[card(c, t) for c, t in CARDS_A],
            *[
                card(c, t, "gap" if k == 0 else "")
                for k, (c, t) in enumerate(CARDS_B)
            ],
            html.Details(className="dev", children=[
                html.Summary("Zones sent to the planner"),
                html.Pre(
                    id="zones_json",
                    style={"maxHeight": "220px", "overflow": "auto", "fontSize": "11px"}
                )
            ])
        ])
    ]),

    html.Div(className="bottombar", children=[
        html.Div([
            html.Div("Copernicus layer", className="lbl"),
            dcc.Dropdown(
                id="layer",
                options=[{"label": label(v), "value": v} for v in VARS],
                value=VARS[0], clearable=False
            )
        ], style={"width": "250px"}),
        html.Div([
            html.Div("Day (grey past, blue today, green forecast)", className="lbl"),
            dcc.Slider(
                id="day", min=0, max=len(DAYS) - 1, step=1,
                value=I0, marks=MARKS, included=False
            )
        ], style={"flex": "1"}),
        html.Div(id="status", className="status")
    ]),

    html.Div(id="zone_note", className="warn2"),
    html.Div(
        id="zone_list",
        style={"maxHeight": "220px", "overflowY": "auto", "padding": "4px 8px"}
    ),
    html.Div(id="mission_summary", className="mission-summary"),

    html.Div(id="help", style=HELP_HIDE, children=html.Div(
        style=HELP_BOX, children=[
            html.Div(
                style={"display": "flex", "gap": "8px", "marginBottom": "8px"},
                children=[
                    html.Button("English", id="help-en", n_clicks=0),
                    html.Button("Ελληνικά", id="help-el", n_clicks=0),
                    html.Button(
                        "Close / Κλείσιμο", id="help-close", n_clicks=0,
                        style={"marginLeft": "auto"}
                    )
                ]
            ),
            html.Div(id="help-body", children=help_body("en"))
        ]
    ))
])


# ---- tutorial open / close / language ------------------------------------------------------------
@app.callback(
    Output("help", "style"), Output("help-body", "children"),
    Input("btn-help", "n_clicks"), Input("help-close", "n_clicks"),
    Input("help-en", "n_clicks"), Input("help-el", "n_clicks"),
    prevent_initial_call=True
)
def help_cb(*_):
    triggered = ctx.triggered_id
    if triggered == "help-close":
        return HELP_HIDE, no_update
    return HELP_SHOW, help_body("el" if triggered == "help-el" else "en")


# ---- Copernicus layer -----------------------------------------------------------------------------
@app.callback(
    Output("overlay", "url"), Output("cbar", "src"), Output("status", "children"),
    Input("layer", "value"), Input("day", "value")
)
def show(var, i):
    return overlay(var, i), cbar(var), status_rows(var, i)


# ---- selected position ---------------------------------------------------------------------------
@app.callback(
    Output("sel", "children"), Output("sel_box", "children"),
    Input("map", "clickData"), Input("layer", "value"), Input("day", "value")
)
def pick(click, var, i):
    if not click:
        return [], "Selected position: click the map"

    lat, lon = latlng(click)
    da = field_at(ds, var, i)
    s = sample(da, lat, lon)
    if s.status == "land":
        s = sample(da, lat, lon, fallback_cells=2)

    val = "-" if s.status in ("land", "outside") else f"{s.value:.3f} mg/m³"
    rows = [
        html.B("Selected position"),
        html.Div(f"X: {lon:.4f}   Y: {lat:.4f}   value: {val}"),
        html.Div(f"{label(var)}, {DAYS[i]} ({rel_day(i)})", className="small")
    ]

    if s.status == "fallback":
        rows.append(html.Div(
            f"Land-masked cell: nearest sea cell, {s.dist_km:.1f} km away. Not a local value.",
            className="warn"
        ))
    elif s.status == "land":
        rows.append(html.Div(
            "No data here (land mask, no sea cell within 2 cells)",
            className="warn"
        ))
    elif s.status == "outside":
        rows.append(html.Div("Outside the Copernicus grid, no data", className="warn"))

    outline = [
        dl.Rectangle(bounds=s.bounds, color="white", weight=2, fill=False)
    ] if s.bounds else []
    return outline, rows


# ---- start position ------------------------------------------------------------------------------
@app.callback(
    Output("pin_mode", "data"), Output("start", "data"),
    Output("btn-pin", "className"), Output("pin_note", "children"),
    Input("btn-pin", "n_clicks"), Input("map", "clickData"),
    State("pin_mode", "data"), State("start", "data"),
    prevent_initial_call=True
)
def pin(_n, click, mode, start):
    if ctx.triggered_id == "btn-pin":
        mode = not mode
        return mode, start, "tool on" if mode else "tool", ""

    if mode and click:
        lat, lon = latlng(click)
        problem = coast_problem(lat, lon)
        if problem:
            return (
                no_update, no_update, no_update,
                f"Start position refused: it {problem}. Click a spot closer to the coast."
            )
        return False, {"lat": lat, "lon": lon}, "tool", ""

    return no_update, no_update, no_update, no_update


@app.callback(Output("start_layer", "children"), Input("start", "data"))
def draw_start(start):
    if not start:
        return []
    return [dl.CircleMarker(
        center=[start["lat"], start["lon"]], radius=8,
        color="#000", weight=2, fillColor="#ffd54a", fillOpacity=1,
        interactive=False,
        children=dl.Tooltip("Start", permanent=True, direction="top")
    )]


@app.callback(
    Output("ship_box", "children"),
    Input("start", "data"), Input("layer", "value"),
    Input("day", "value"), Input("last_mission", "data")
)
def ship_box(start, var, i, last_mission):
    rows = [
        html.B("Current ship position"),
        html.Div("X: -   Y: -   value: -  (run a mission to connect the simulator)")
    ]

    if last_mission and last_mission.get("final_position"):
        position = last_mission["final_position"]
        rows = [
            html.B("Current ship position"),
            html.Div(
                f"X: {position['longitude']:.4f}   "
                f"Y: {position['latitude']:.4f}  (last simulation)"
            )
        ]

    if start:
        s = sample(field_at(ds, var, i), start["lat"], start["lon"], fallback_cells=2)
        value = "-" if s.status in ("land", "outside") else f"{s.value:.3f}"
        rows.append(html.Div(
            f"Planned start  X: {start['lon']:.4f}   "
            f"Y: {start['lat']:.4f}   value: {value}",
            className="small"
        ))
        if s.status in ("land", "outside"):
            rows.append(html.Div("Start has no Copernicus value here", className="warn"))

    return rows


# ---- zoom buttons --------------------------------------------------------------------------------
@app.callback(
    Output("map", "viewport"),
    Input("btn-fit", "n_clicks"), Input("btn-zoom", "n_clicks"),
    State("zones", "data"), State("start", "data"), State("map", "clickData"),
    prevent_initial_call=True
)
def zoom(_a, _b, zones, start, click):
    if ctx.triggered_id == "btn-fit":
        bounds = zone_bounds(zones[0]) if zones else BOUNDS
        return {"bounds": bounds, "transition": "flyToBounds"}

    if click:
        lat, lon = latlng(click)
    elif start:
        lat, lon = start["lat"], start["lon"]
    else:
        return no_update

    return {"center": [lat, lon], "zoom": 10, "transition": "flyTo"}


# ---- mission map layers --------------------------------------------------------------------------
def _mission_route_layer(plan_history):
    layers = []
    for index, item in enumerate(plan_history or []):
        plan = item.get("plan", {})
        positions = [
            [wp["latitude"], wp["longitude"]]
            for wp in plan.get("waypoints", [])
        ]
        if len(positions) >= 2:
            layers.append(dl.Polyline(
                positions=positions,
                color="#00d5ff" if index == 0 else "#ffbf47",
                weight=4 if index == 0 else 3,
                opacity=0.9,
                dashArray=None if index == 0 else "7 6",
                children=dl.Tooltip(
                    "Initial planned route" if index == 0
                    else f"Replan: {item.get('reason', 'updated route')}"
                )
            ))
    return layers


def _mission_measurement_layer(run_result):
    import csv

    rows = []
    path = run_result.get("output_dir")
    if path:
        try:
            with open(
                f"{path}/crosscheck_samples.csv",
                newline="", encoding="utf-8"
            ) as handle:
                rows = list(csv.DictReader(handle))
        except OSError:
            rows = []

    if not rows:
        return []

    stride = max(1, len(rows) // 220)
    layers = []
    for row in rows[::stride]:
        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
        except (TypeError, ValueError, KeyError):
            continue

        layers.append(dl.CircleMarker(
            center=[lat, lon], radius=2.5,
            color="#d2f5ff", fillColor="#00bfe8",
            fillOpacity=0.8, weight=1,
            children=dl.Tooltip("ASV measurement")
        ))
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

        layers.append(dl.CircleMarker(
            center=[lat, lon], radius=8,
            color="#ff3030", fillColor="#ff3030",
            fillOpacity=0.85, weight=2,
            children=dl.Tooltip(
                f"Divergence: bias {bias:+.3f} mg/m³; n={cell.get('count', 0)}"
            )
        ))
    return layers


# ---- end-to-end mission --------------------------------------------------------------------------
@app.callback(
    Output("run", "data"), Output("btn-run-img", "src"),
    Output("route_layer", "children"), Output("samples_layer", "children"),
    Output("divergence_layer", "children"), Output("mission_summary", "children"),
    Output("last_mission", "data"), Output("card-battery", "children"),
    Output("card-autonomy", "children"), Output("card-mode", "children"),
    Output("card-comms", "children"), Output("card-route", "children"),
    Output("card-loop", "children"),
    Input("btn-run", "n_clicks"),
    State("start", "data"), State("zones", "data"),
    State("layer", "value"), State("speed", "data"),
    prevent_initial_call=True
)
def run(_n, start, zones, variable, speed):
    empty = (
        False, icons.PLAY, [], [], [], html.Div(), None,
        "-", "-", "-", "-", "-", "-"
    )

    if not start:
        return (
            *empty[:5],
            html.Div(
                "Set a launch position first: click the pin tool, then click the map.",
                className="warn"
            ),
            {"status": "not_started", "error": "No start position was set."},
            *empty[7:]
        )

    if not zones or len(zones) < 2:
        return (
            *empty[:5],
            html.Div([
                html.B("Cannot start the mission. "),
                "You need one background rectangle (the area of interest) and at least one "
                "monitoring rectangle drawn INSIDE it.",
                html.Div(
                    "The 'Zones sent to the planner' panel on the right shows what the UI sees. "
                    "If it shows only one zone, draw a second rectangle fully inside the first one.",
                    className="small"
                )
            ], className="warn"),
            {"status": "not_started", "error": "Fewer than 2 zones."},
            *empty[7:]
        )

    outside = [
        k for k, z in enumerate(zones[1:], start=1)
        if zone_outside(z, zones[0])
    ]
    if outside:
        return (
            *empty[:5],
            html.Div([
                html.B("Cannot start the mission. "),
                f"Zone(s) {', '.join(str(k + 1) for k in outside)} "
                "extend outside the area of interest (Zone 1).",
                html.Div(
                    "Delete those zones with the × button, or redraw them fully inside Zone 1.",
                    className="small"
                )
            ], className="warn"),
            {"status": "not_started", "error": "Zones outside the area of interest."},
            *empty[7:]
        )

    bad = [
        f"Zone {k + 1} {zone_coast_problem(z)}"
        for k, z in enumerate(zones)
        if zone_coast_problem(z)
    ]
    if bad:
        return (
            *empty[:5],
            html.Div([
                html.B("Cannot start the mission. "),
                html.Ul([html.Li(item) for item in bad])
            ], className="warn"),
            {"status": "not_started", "error": "Zones violate the coast rule."},
            *empty[7:]
        )

    spacing = {"fast": 40.0, "medium": 20.0, "slow": 5.0}.get(speed, 20.0)
    payload = {
        "zones": zones,
        "start": {"lat": start.get("lat"), "lon": start.get("lon")},
        "variable": variable,
        "budget_meters": 30000.0,
        "buffer_meters": 50.0,
        "sample_spacing_meters": spacing,
        "coast_rule_km": 3.0,
        "enforce_coast_rule": True,
        "divergence_threshold_mg_m3": 0.15
    }

    try:
        result = run_mission(
            payload, output_root=config.ROOT / "runs", dataset=ds
        )
    except Exception as error:
        error_result = {"status": "error", "error": str(error)}
        return (
            False, icons.PLAY, [], [], [],
            html.Div([html.B("Mission could not start. "), str(error)], className="warn"),
            error_result, "-", "-", "-", "-", "-", "-"
        )

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
        html.Span(
            f"{result.get('sample_count', 0)} samples; "
            f"{cells_flagged}/{cells_compared} Copernicus cells flagged; "
        ),
        html.Span(f"global sensor-minus-product bias {bias_text}."),
        html.Div(
            f"Replans: {len(result.get('replans', []))} · "
            f"stop: {result.get('stop_reason', result.get('message', 'unknown'))}"
        ),
        html.Div(
            f"Artifacts: {result.get('output_dir', 'not written')}",
            className="small"
        )
    ])

    route_layers = _mission_route_layer(plan_history)
    sample_layers = _mission_measurement_layer(result)
    divergence_layers = _mission_divergence_layer(result)

    total = max(1, len(
        plan_history[0].get("plan", {}).get("covered_subareas", [])
        if plan_history else []
    ))
    progress = (
        f"{100.0 * len(result.get('completed_subareas', [])) / total:.0f}%"
    )
    remaining_h = max(
        0.0,
        float(result.get("remaining_budget_m", 0.0)) / 1.028888 / 3600.0
    )
    first_plan = plan_history[0].get("plan", {}) if plan_history else {}
    loop_h = float(first_plan.get("estimated_duration_s", 0.0)) / 3600.0

    battery = result.get("final_battery_fraction")
    battery_text = "-" if battery is None else f"{100 * float(battery):.0f}%"
    status = result.get("status", "unknown")

    return (
        False, icons.PLAY, route_layers, sample_layers, divergence_layers,
        message, result, battery_text, f"{remaining_h:.1f} h",
        result.get("final_mode", status), "connected (simulated)",
        progress, f"{loop_h:.1f} h"
    )


@app.callback(
    Output("hint", "children"),
    Input("pin_mode", "data"), Input("run", "data"),
    Input("last_mission", "data")
)
def hint(pin_mode, running, last_mission):
    if pin_mode:
        return "Click the map to place the start position."
    if running:
        return "Mission running."
    if last_mission and last_mission.get("status") == "complete":
        return (
            "Mission complete: cyan circles are samples; red circles identify "
            "Copernicus cells with divergence."
        )
    if last_mission and last_mission.get("error"):
        return "Mission was not started; check the message below."
    return (
        "Draw ONE big background rectangle first, then at least one monitoring "
        "rectangle INSIDE it. Pin a start, then press Start. Press ? for instructions."
    )


# ---- selection pills (speed / mode) ---------------------------------------------------------------
def wire_segmented(prefix, options, store=None):
    ids = [f"{prefix}-{value}" for value, _ in options]

    @app.callback(
        Output(store or prefix, "data"),
        *[Output(component_id, "className") for component_id in ids],
        *[Input(component_id, "n_clicks") for component_id in ids],
        prevent_initial_call=True
    )
    def _pick(*_):
        chosen = ctx.triggered_id.split("-", 1)[1]
        return (
            chosen,
            *[
                "seg on" if value == chosen else "seg"
                for value, _ in options
            ]
        )


wire_segmented("speed", SPEED)
wire_segmented("mode", MODES, store="mode_cmd")


@app.callback(
    Output("card-mode", "children", allow_duplicate=True),
    Input("mode_cmd", "data"),
    prevent_initial_call=True
)
def mode_card(mode):
    return f"{mode} (commanded)"


# ---- zone helpers --------------------------------------------------------------------------------
def _zone_row(text, zone_k, rejected=False):
    style = {
        "display": "flex", "justifyContent": "space-between",
        "alignItems": "center", "padding": "4px 8px",
        "borderBottom": "1px solid #eee",
        "color": "#c33" if rejected else "#1b2430"
    }
    btn_style = {
        "background": "#c33", "color": "white", "border": "none",
        "borderRadius": "4px", "cursor": "pointer",
        "padding": "2px 10px", "fontSize": "14px"
    }
    return html.Div(style=style, children=[
        html.Span(text),
        html.Button(
            "×", id={"type": "zone-del", "index": zone_k},
            n_clicks=0, style=btn_style,
            title="Delete this area (UI only; use the map's trash icon to remove it from the map)"
        )
    ])


# ---- zones (rectangles drawn by the user) ---------------------------------------------------------
@app.callback(
    Output("zones", "data"), Output("zone_layer", "children"),
    Output("zones_json", "children"), Output("zone_note", "children"),
    Output("zone_list", "children"),
    Input("edit", "geojson"), Input("hidden_zones", "data")
)
def zones_cb(gj, hidden):
    hidden = set(hidden or [])
    features = list((gj or {}).get("features", []))
    if features:
        print(
            f"[zones_cb] raw features: {len(features)}, "
            f"hidden indices: {sorted(hidden)}"
        )

    kept_features = [
        feature for index, feature in enumerate(features)
        if index not in hidden
    ]

    if not features:
        zones = []
    else:
        try:
            zones = zones_from_geojson({
                "type": "FeatureCollection",
                "features": kept_features
            })
        except Exception as exc:
            print(f"[zones_cb] zones_from_geojson failed: {exc!r}")
            zones = []

    print(
        f"[zones_cb] kept features: {len(kept_features)}, zones: {len(zones)}"
    )

    layers, notes, rows = [], [], []
    first_zone = zones[0] if zones else None

    for k, zone in enumerate(zones):
        try:
            bounds = zone_bounds(zone)
        except Exception:
            continue

        outside = (
            first_zone is not None and k > 0
            and zone_outside(zone, first_zone)
        )

        if outside:
            layers.append(dl.Rectangle(
                bounds=bounds, color="#ff3030", weight=2,
                dashArray="4 4", fillColor="#ff3030",
                fillOpacity=0.15, interactive=False,
                children=dl.Tooltip("Ignored: outside the area of interest")
            ))
        elif k > 0:
            layers.append(dl.Rectangle(
                bounds=bounds, weight=0, fillColor="white",
                fillOpacity=0.3, interactive=False
            ))

        coast_problem_text = zone_coast_problem(zone)
        grid_problem = (
            None if inside_grid(zone, BOUNDS)
            else "is partly outside the Copernicus grid"
        )

        problems = []
        if outside:
            problems.append("is outside the area of interest (Zone 1)")
        problems.extend(
            problem for problem in (coast_problem_text, grid_problem)
            if problem
        )

        if problems:
            notes.append(f"Zone {k + 1} " + "; ".join(problems) + ". ")

        label_text = f"Zone {k + 1}" + (
            " (area of interest)" if k == 0 else ""
        )
        if problems:
            label_text += " ⚠ " + "; ".join(problems)

        rows.append(_zone_row(label_text, k, rejected=outside))

    if len(zones) == 0:
        rows.append(html.Div(
            "No zones yet. Draw a rectangle with the pencil → rectangle tool on the map.",
            style={"color": "#c33", "padding": "4px 8px"}
        ))
    elif len(zones) == 1:
        rows.append(html.Div(
            "Draw at least one monitoring rectangle INSIDE the first rectangle.",
            style={"color": "#c33", "padding": "4px 8px"}
        ))

    return zones, layers, json.dumps(zones, indent=1), "".join(notes), rows


# ---- per-zone delete (UI only) -------------------------------------------------------------------
@app.callback(
    Output("hidden_zones", "data"),
    Input({"type": "zone-del", "index": ALL}, "n_clicks"),
    State("hidden_zones", "data"),
    State("edit", "geojson"),
    prevent_initial_call=True
)
def delete_zone(n_clicks_list, hidden, gj):
    if not ctx.triggered:
        return no_update

    trigger = ctx.triggered[0]
    if not trigger.get("value"):
        return no_update

    triggered_id = ctx.triggered_id
    if not isinstance(triggered_id, dict) or triggered_id.get("type") != "zone-del":
        return no_update

    zone_k = triggered_id.get("index")
    if not isinstance(zone_k, int):
        return no_update

    hidden = set(hidden or [])
    features = list((gj or {}).get("features", []))

    # Map visible-zone index to original GeoJSON feature index.
    visible_index = 0
    for feature_index in range(len(features)):
        if feature_index in hidden:
            continue
        if visible_index == zone_k:
            hidden.add(feature_index)
            return sorted(hidden)
        visible_index += 1

    return no_update


if __name__ == "__main__":
    app.run(debug=False)