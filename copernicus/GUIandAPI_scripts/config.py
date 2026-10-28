from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# bbox = (lon_min, lat_min, lon_max, lat_max), or None = the dataset's full extent.
# "variables": the first one is the primary layer (used by the validator and the ship circles).
PRESETS = {
    "med_oc_1km": dict(
        dataset_id="PASTE_ID_FROM_DESCRIBE",   # e.g. cmems_obs-oc_med_bgc-plankton_nrt_l4-gapfree-multi-1km_P1D
        variables=("CHL",),                    # check with inspect_dataset
        has_depth=False, forecast_days=0,
        bbox=(2.4, 39.4, 2.8, 39.7), zoom=11, center=None,
    ),
    # Old small box near Mallorca. Kept so its cache file stays usable.
    "med_bgc_4km": dict(
        dataset_id="cmems_mod_med_bgc-pft_anfc_4.2km_P1D-m",   # verified with describe + inspect (version 202511)
        variables=("chl",),
        has_depth=True, forecast_days=5,
        bbox=(2.0, 39.0, 3.0, 40.0), zoom=9, center=None,
    ),
    # Whole Mediterranean (full extent of the dataset): total chlorophyll + 4 plankton groups.
    # All five names were seen in the verified inspect_dataset output.
    "med_bgc_4km_full": dict(
        dataset_id="cmems_mod_med_bgc-pft_anfc_4.2km_P1D-m",
        variables=("chl", "diatoChla", "dinoChla", "nanoChla", "picoChla"),
        has_depth=True, forecast_days=10,      # clipped automatically to the last day the server has
        bbox=None, zoom=5, center=(38.0, 15.0),
    ),
    "glo_bgc_025": dict(
        dataset_id="PASTE_ID_FROM_DESCRIBE",   # e.g. cmems_mod_glo_bgc-pft_anfc_0.25deg_P1D-m
        variables=("chl",),
        has_depth=True, forecast_days=5,
        bbox=(1.5, 38.5, 3.5, 40.5), zoom=8, center=None,
    ),
}
ACTIVE = "med_bgc_4km_full"
P = PRESETS[ACTIVE]

DATASET_ID = P["dataset_id"]
VARIABLES = P["variables"]
VARIABLE = VARIABLES[0]                          # primary layer (kept for older scripts)
HAS_DEPTH = P["has_depth"]
BBOX = P["bbox"]
ZOOM = P["zoom"]
CENTER = P["center"]                             # None = centre of the data
MIN_DEPTH, MAX_DEPTH = 1.0, 2.0                  # surface layer only (first model level is 1.018 m)

HISTORY_DAYS = 7
TODAY = date.today()
START = (TODAY - timedelta(days=HISTORY_DAYS)).isoformat()
END = (TODAY + timedelta(days=P["forecast_days"])).isoformat()

CACHE_FILE = ROOT / "data_cache" / f"{ACTIVE}.nc"

# Primary layer: FIXED log scale (mg/m3), same for map, colourbar and ship circles. Never autoscale this one.
VMIN, VMAX, CMAP = 0.03, 3.0, "viridis"

# Secondary "explore" layers are autoscaled once over the whole cache (see render.layer_scale).
LAYER_LABELS = {
    "chl": "Total chlorophyll",
    "diatoChla": "Diatoms (chl)",
    "dinoChla": "Dinoflagellates (chl)",
    "nanoChla": "Nanophytoplankton (chl)",
    "picoChla": "Picophytoplankton (chl)",
}

# Zones drawn in the UI. First rectangle = background area of interest, every further one stacks on top.
# "weight" is the rectangle's own contribution; the planner sums the weights of all zones covering a point.
BG_WEIGHT = 1
STACK_WEIGHT = 1
