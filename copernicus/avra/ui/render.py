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

try:
    # Offline land/sea mask.
    from global_land_mask import globe
except ImportError:
    globe = None


OVERSAMPLE = 4
COAST_FILL_CELLS = 2


def _merc(lat):
    lat = np.clip(lat, -85.0, 85.0)
    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def _unmerc(y):
    return np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)


def _uri(png_bytes):
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


def grid_bounds(da):
    """Leaflet bounds [[south, west], [north, east]]; coordinates are cell centres."""
    lat = np.asarray(da.latitude.values)
    lon = np.asarray(da.longitude.values)
    dlat = abs(float(lat[1] - lat[0])) / 2 if len(lat) > 1 else 0.0
    dlon = abs(float(lon[1] - lon[0])) / 2 if len(lon) > 1 else 0.0
    return [
        [float(lat.min()) - dlat, float(lon.min()) - dlon],
        [float(lat.max()) + dlat, float(lon.max()) + dlon],
    ]


def layer_scale(ds, variable):
    """Fixed scale for the primary layer; robust autoscale for other layers."""
    if variable == config.VARIABLE:
        return config.VMIN, config.VMAX

    values = ds[variable].isel(time=slice(None, None, 3)).values
    values = values[np.isfinite(values) & (values > 0)]
    if values.size == 0:
        return config.VMIN, config.VMAX

    lo, hi = np.percentile(values, [1, 99])
    vmin = 10 ** np.floor(np.log10(lo))
    vmax = 10 ** np.ceil(np.log10(hi))
    if vmax / vmin < 100:
        vmin = vmax / 100
    return max(vmin, vmax / 1e4), vmax


def _fill_coast(vals, n):
    """Display-only fill of nearby land-masked cells from neighbouring sea values."""
    v = vals.astype(np.float32).copy()
    for _ in range(n):
        pad = np.pad(v, 1, constant_values=np.nan)
        neighbours = np.stack([
            pad[1 + di:1 + di + v.shape[0],
                1 + dj:1 + dj + v.shape[1]]
            for di in (-1, 0, 1)
            for dj in (-1, 0, 1)
            if (di, dj) != (0, 0)
        ])
        valid = np.isfinite(neighbours)
        count = valid.sum(axis=0)
        fill = np.isnan(v) & (count > 0)
        if not fill.any():
            break
        total = np.where(valid, neighbours, 0).sum(axis=0)
        v[fill] = total[fill] / count[fill]
    return v


@lru_cache(maxsize=4)
def _geometry(top, dlat, n, west, dlon, m):
    """Cached pixel-to-cell maps and land mask for the image overlay."""
    N, M = n * OVERSAMPLE, m * OVERSAMPLE
    bottom, east = top - n * dlat, west + m * dlon

    y = _merc(top) + (
        (np.arange(N) + 0.5) / N
    ) * (_merc(bottom) - _merc(top))
    lat_px = _unmerc(y)
    lon_px = west + (np.arange(M) + 0.5) / M * (east - west)

    rows = np.clip(((top - lat_px) / dlat).astype(int), 0, n - 1)
    cols = np.clip(((lon_px - west) / dlon).astype(int), 0, m - 1)

    land = (
        globe.is_land(lat_px[:, None], lon_px[None, :])
        if globe is not None else None
    )
    return rows, cols, land


def field_to_png(da, vmin, vmax, cmap=None):
    """Convert a 2D latitude/longitude field to a transparent Web-Mercator PNG."""
    cm = colormaps[cmap or config.CMAP].copy()
    cm.set_bad((0, 0, 0, 0))

    da = da.sortby("latitude", ascending=False)
    lat = da.latitude.values
    lon = da.longitude.values

    if len(lat) < 2 or len(lon) < 2:
        raise ValueError("field_to_png requires at least two latitude and longitude coordinates.")

    dlat = abs(float(lat[0] - lat[1]))
    dlon = abs(float(lon[1] - lon[0]))

    vals = _fill_coast(da.values, COAST_FILL_CELLS) if globe is not None else da.values
    rgba = (
        cm(LogNorm(vmin, vmax, clip=True)(np.ma.masked_invalid(vals))) * 255
    ).astype(np.uint8)

    rows, cols, land = _geometry(
        float(lat[0]) + dlat / 2, dlat, len(lat),
        float(lon[0]) - dlon / 2, dlon, len(lon)
    )

    img = rgba[rows][:, cols].copy()
    if land is not None:
        img[land, 3] = 0

    buf = io.BytesIO()
    Image.fromarray(img, "RGBA").save(buf, format="PNG", compress_level=3)
    return _uri(buf.getvalue()), grid_bounds(da)


def colourbar_uri(vmin, vmax, unit="mg/m³", cmap=None):
    """Create the vertical logarithmic colourbar as a PNG data URI."""
    fig = Figure(figsize=(1.0, 4.6), dpi=100)
    ax = fig.add_axes([0.14, 0.03, 0.26, 0.88])

    cb = fig.colorbar(
        ScalarMappable(
            norm=LogNorm(vmin, vmax),
            cmap=colormaps[cmap or config.CMAP]
        ),
        cax=ax
    )

    ticks = [
        multiplier * 10.0 ** exponent
        for exponent in range(
            int(np.floor(np.log10(vmin))),
            int(np.ceil(np.log10(vmax))) + 1
        )
        for multiplier in (1, 3)
        if vmin * 0.999 <= multiplier * 10.0 ** exponent <= vmax * 1.001
    ]
    cb.set_ticks(ticks, labels=[f"{tick:g}" for tick in ticks])
    cb.ax.minorticks_off()
    fig.text(0.02, 0.95, unit, fontsize=9)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    return _uri(buf.getvalue())


def coastline_segments(bounds, coast_km=3.0, coverage_da=None):
    """
    Extract coastline from the boundary of Copernicus-covered sea cells.

    The coastline is retained only where the covered-sea boundary touches
    land. The offshore contour is calculated from those nearby coastal
    land cells and masked everywhere outside covered sea.

    Returns lists of Leaflet [latitude, longitude] segments.
    """
    if globe is None or coverage_da is None:
        return [], []

    (south, west), (north, east) = bounds
    if north <= south or east <= west:
        return [], []

    step = max(0.005, min(
        0.02,
        np.sqrt((north - south) * (east - west) / 1_500_000)
    ))

    lat = np.arange(south, north + step, step)
    lon = np.arange(west, east + step, step)
    if len(lat) < 2 or len(lon) < 2:
        return [], []

    # Land/sea mask over the plotting grid.
    land = globe.is_land(lat[:, None], lon[None, :])

    # Remove only singleton non-spatial dimensions (e.g. depth=1).
    extra_dims = [
        dim for dim in coverage_da.dims
        if dim not in ("latitude", "longitude")
    ]
    for dim in extra_dims:
        if coverage_da.sizes[dim] != 1:
            raise ValueError(
                f"Coverage must be 2D after reducing singleton dimensions; "
                f"{dim} has size {coverage_da.sizes[dim]}."
            )
        coverage_da = coverage_da.isel({dim: 0})

    coverage_da = (
        coverage_da.sortby("latitude")
        .sortby("longitude")
        .transpose("latitude", "longitude")
    )

    src_lat = np.asarray(coverage_da.latitude.values)
    src_lon = np.asarray(coverage_da.longitude.values)
    valid = np.isfinite(coverage_da.values)

    if len(src_lat) == 0 or len(src_lon) == 0:
        return [], []
    if valid.shape != (len(src_lat), len(src_lon)):
        raise ValueError(
            f"Coverage shape mismatch: {valid.shape}, "
            f"latitude={len(src_lat)}, longitude={len(src_lon)}"
        )

    def nearest_indices(source, target):
        if len(source) == 1:
            return np.zeros(len(target), dtype=int)
        idx = np.searchsorted(source, target)
        idx = np.clip(idx, 1, len(source) - 1)
        left, right = idx - 1, idx
        return np.where(
            np.abs(target - source[left]) <= np.abs(target - source[right]),
            left, right
        )

    ilat = nearest_indices(src_lat, lat)
    ilon = nearest_indices(src_lon, lon)

    # Do not extrapolate the edge cells beyond the Copernicus grid.
    inside_grid = (
        (lat[:, None] >= src_lat.min())
        & (lat[:, None] <= src_lat.max())
        & (lon[None, :] >= src_lon.min())
        & (lon[None, :] <= src_lon.max())
    )
    coverage = valid[np.ix_(ilat, ilon)] & inside_grid

    # Valid sea cells only.
    sea = (~land) & coverage
    if not sea.any():
        return [], []

    # Find land cells immediately adjacent to Copernicus-covered sea.
    padded_sea = np.pad(sea, 1, constant_values=False)
    adjacent_sea = np.zeros_like(land, dtype=bool)

    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if di == 0 and dj == 0:
                continue
            adjacent_sea |= padded_sea[
                1 + di:1 + di + len(lat),
                1 + dj:1 + dj + len(lon)
            ]

    coastal_land = land & adjacent_sea
    if not coastal_land.any():
        return [], []

    # Approximate distance in km from coastal land.
    lat_km = step * 111.32
    lon_km = max(
        step * 111.32 * np.cos(np.radians((south + north) / 2)),
        0.001
    )

    distance = np.full(land.shape, np.inf)
    distance[coastal_land] = 0.0

    iterations = int(np.ceil(coast_km / min(lat_km, lon_km))) + 2
    for _ in range(iterations):
        previous = np.pad(distance, 1, constant_values=np.inf)
        best = distance.copy()

        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue

                shifted = previous[
                    1 + di:1 + di + len(lat),
                    1 + dj:1 + dj + len(lon)
                ]
                step_distance = np.hypot(di * lat_km, dj * lon_km)
                best = np.minimum(best, shifted + step_distance)

        # Propagate only through valid Copernicus-covered sea.
        distance[sea] = best[sea]

    fig = Figure()
    ax = fig.subplots()

    # Preferred coastline approach: contour the covered-sea boundary,
    # then retain only portions that are adjacent to land.
    sea_boundary = ax.contour(lon, lat, sea.astype(float), levels=[0.5])
    coastline = []

    for segment in sea_boundary.allsegs[0]:
        if len(segment) < 2:
            continue

        rows = nearest_indices(lat, segment[:, 1])
        cols = nearest_indices(lon, segment[:, 0])
        keep = []

        for row, col in zip(rows, cols):
            r0, r1 = max(0, row - 1), min(len(lat), row + 2)
            c0, c1 = max(0, col - 1), min(len(lon), col + 2)
            keep.append(land[r0:r1, c0:c1].any())

        start = None
        for j, ok in enumerate(keep):
            if ok and start is None:
                start = j
            elif not ok and start is not None:
                if j - start >= 2:
                    coastline.append([
                        [float(y), float(x)] for x, y in segment[start:j]
                    ])
                start = None

        if start is not None and len(segment) - start >= 2:
            coastline.append([
                [float(y), float(x)] for x, y in segment[start:]
            ])

    # Offshore contour: strictly masked outside covered sea.
    masked_distance = np.ma.masked_where(
        (~sea) | (~np.isfinite(distance)),
        distance
    )
    offshore_contour = ax.contour(
        lon, lat, masked_distance, levels=[coast_km]
    )

    offshore = []
    for segment in offshore_contour.allsegs[0]:
        if len(segment) < 2:
            continue
        offshore.append([
            [float(y), float(x)] for x, y in segment
        ])

    fig.clear()
    return coastline, offshore