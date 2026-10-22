import base64
import io
from functools import lru_cache

import numpy as np
from matplotlib import colormaps
from PIL import Image
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from avra import config

try:                                   # ~1 km land/sea mask that ships inside the package (works offline)
    from global_land_mask import globe
except ImportError:                    # app still works, just with the jagged coast
    globe = None

OVERSAMPLE = 4       # output pixels per data cell, both directions (Mercator rows + smooth coast)
COAST_FILL_CELLS = 2  # display only: paint land-masked cells up to this many cells away from the sea


def _merc(lat):
    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def _unmerc(y):
    return np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)


def _uri(png_bytes):
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


def grid_bounds(da):
    """Leaflet bounds [[south, west], [north, east]] of the whole grid (coordinates are cell centres)."""
    lat, lon = da.latitude.values, da.longitude.values
    dlat, dlon = abs(float(lat[1] - lat[0])) / 2, abs(float(lon[1] - lon[0])) / 2
    return [[float(lat.min()) - dlat, float(lon.min()) - dlon],
            [float(lat.max()) + dlat, float(lon.max()) + dlon]]


def layer_scale(ds, variable):
    """(vmin, vmax). Primary layer: fixed from config. Others: autoscaled once over all days."""
    if variable == config.VARIABLE:
        return config.VMIN, config.VMAX
    v = ds[variable].isel(time=slice(None, None, 3)).values
    v = v[np.isfinite(v) & (v > 0)]
    if v.size == 0:
        return config.VMIN, config.VMAX
    lo, hi = np.percentile(v, [1, 99])
    vmin, vmax = 10 ** np.floor(np.log10(lo)), 10 ** np.ceil(np.log10(hi))
    if vmax / vmin < 100:
        vmin = vmax / 100
    return max(vmin, vmax / 1e4), vmax


def _fill_coast(vals, n):
    """Display only. Cells that touch land are masked in the product, which leaves an uncoloured strip
    along the coast. Give those cells (up to n cells from the sea) the mean of their sea neighbours."""
    v = vals.astype(np.float32).copy()
    for _ in range(n):
        pad = np.pad(v, 1, constant_values=np.nan)
        nb = np.stack([pad[1 + di:1 + di + v.shape[0], 1 + dj:1 + dj + v.shape[1]]
                       for di in (-1, 0, 1) for dj in (-1, 0, 1) if (di, dj) != (0, 0)])
        ok = np.isfinite(nb)
        cnt = ok.sum(0)
        fill = np.isnan(v) & (cnt > 0)
        v[fill] = (np.where(ok, nb, 0).sum(0)[fill] / cnt[fill])
    return v


@lru_cache(maxsize=4)
def _geometry(top, dlat, n, west, dlon, m):
    """Pixel -> data-cell index maps and the land mask of the output image. Same for every day/layer."""
    N, M = n * OVERSAMPLE, m * OVERSAMPLE
    bottom, east = top - n * dlat, west + m * dlon
    y = _merc(top) + (np.arange(N) + 0.5) / N * (_merc(bottom) - _merc(top))
    lat_px = _unmerc(y)                                          # latitude of each output row
    lon_px = west + (np.arange(M) + 0.5) / M * (east - west)     # longitude of each output column
    rows = np.clip(((top - lat_px) / dlat).astype(int), 0, n - 1)
    cols = np.clip(((lon_px - west) / dlon).astype(int), 0, m - 1)
    land = globe.is_land(lat_px[:, None], lon_px[None, :]) if globe is not None else None
    return rows, cols, land


def field_to_png(da, vmin, vmax, cmap=None):
    """2D (latitude, longitude) field -> (data-URI PNG, Leaflet bounds).

    1) Leaflet stretches an image linearly in Web-Mercator y but the model grid is linear in latitude;
       over the whole Mediterranean that shifts features by tens of km. Rows are therefore resampled
       (nearest neighbour, so cells stay crisp blocks) to Mercator spacing.
    2) Coast: land-masked cells are filled from the nearest sea (display only) and the image is then clipped
       with a ~1 km land mask, so the edge follows the real coastline instead of the 4 km staircase."""
    cm = colormaps[cmap or config.CMAP].copy()
    cm.set_bad((0, 0, 0, 0))                                    # NaN -> transparent
    da = da.sortby("latitude", ascending=False)                  # image row 0 must be north
    lat, lon = da.latitude.values, da.longitude.values
    dlat, dlon = abs(float(lat[0] - lat[1])), abs(float(lon[1] - lon[0]))
    vals = _fill_coast(da.values, COAST_FILL_CELLS) if globe is not None else da.values
    rgba = (cm(LogNorm(vmin, vmax, clip=True)(np.ma.masked_invalid(vals))) * 255).astype(np.uint8)
    rows, cols, land = _geometry(float(lat[0]) + dlat / 2, dlat, len(lat),
                                 float(lon[0]) - dlon / 2, dlon, len(lon))
    img = rgba[rows][:, cols]
    if land is not None:
        img[land, 3] = 0
    buf = io.BytesIO()
    Image.fromarray(img, "RGBA").save(buf, format="PNG", compress_level=3)
    return _uri(buf.getvalue()), grid_bounds(da)


def colourbar_uri(vmin, vmax, unit="mg/m³", cmap=None):
    """Vertical log colourbar (like the mockup, left side) as a data-URI PNG."""
    fig = Figure(figsize=(1.0, 4.6), dpi=100)
    ax = fig.add_axes([0.14, 0.03, 0.26, 0.88])
    cb = fig.colorbar(ScalarMappable(norm=LogNorm(vmin, vmax), cmap=colormaps[cmap or config.CMAP]), cax=ax)
    ticks = [m * 10.0 ** e
             for e in range(int(np.floor(np.log10(vmin))), int(np.ceil(np.log10(vmax))) + 1)
             for m in (1, 3) if vmin * 0.999 <= m * 10.0 ** e <= vmax * 1.001]
    cb.set_ticks(ticks, labels=[f"{t:g}" for t in ticks])
    cb.ax.minorticks_off()
    fig.text(0.02, 0.95, unit, fontsize=9)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    return _uri(buf.getvalue())
