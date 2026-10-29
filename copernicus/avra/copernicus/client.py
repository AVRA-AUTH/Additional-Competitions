from dataclasses import dataclass
import numpy as np
import xarray as xr
try:
    import copernicusmarine as cm
except ImportError:  # cached NetCDF files can be used offline without the download client
    cm = None
from avra import config


def _open(path):
    ds = xr.open_dataset(path)
    ren = {k: v for k, v in {"lat": "latitude", "lon": "longitude"}.items()
           if k in ds.coords or k in ds.dims}
    return ds.rename(ren) if ren else ds


def _last_available_day():
    if cm is None:
        raise RuntimeError("Copernicus Marine toolbox is required to refresh the data cache")
    full = cm.open_dataset(dataset_id=config.DATASET_ID)    # lazy, metadata only
    return np.datetime64(str(full.time.max().values)[:10])


def fetch(refresh=False):
    """Download once, then read from the local cache. refresh=True re-downloads."""
    if config.CACHE_FILE.exists() and not refresh:
        return _open(config.CACHE_FILE)
    if cm is None:
        raise RuntimeError("Copernicus Marine toolbox is required to fetch data; install requirements.txt")
    config.CACHE_FILE.parent.mkdir(exist_ok=True)
    end = min(np.datetime64(config.END), _last_available_day())   # never ask past the last forecast day
    kwargs = dict(dataset_id=config.DATASET_ID, variables=list(config.VARIABLES),
                  start_datetime=config.START, end_datetime=str(end))
    if config.BBOX is not None:
        kwargs.update(minimum_longitude=config.BBOX[0], minimum_latitude=config.BBOX[1],
                      maximum_longitude=config.BBOX[2], maximum_latitude=config.BBOX[3])
    if config.HAS_DEPTH:
        kwargs.update(minimum_depth=config.MIN_DEPTH, maximum_depth=config.MAX_DEPTH)
    ds = cm.open_dataset(**kwargs)
    tmp = config.CACHE_FILE.with_suffix(".tmp.nc")
    ds.to_netcdf(tmp, encoding={v: {"zlib": True, "complevel": 4} for v in ds.data_vars})
    tmp.replace(config.CACHE_FILE)
    return _open(config.CACHE_FILE)


def days(ds):
    """Daily time axis as datetime64[D]."""
    return ds.time.values.astype("datetime64[D]")


def field_at(ds, variable, i):
    """2D (latitude, longitude) slice for time index i, loaded into memory."""
    da = ds[variable]
    if "depth" in da.dims:
        da = da.isel(depth=0)
    return da.isel(time=int(i)).transpose("latitude", "longitude").load()


def latest_field(ds, when=None, variable=None):
    """2D slice closest to 'when' (default now) that has valid data.
    Works for observation products and for forecasts (picks today, not the last forecast day)."""
    da = ds[variable or config.VARIABLE]
    if "depth" in da.dims:
        da = da.isel(depth=0)
    when = np.datetime64("now") if when is None else np.datetime64(when)
    dist = np.abs((da.time.values - when).astype("timedelta64[s]").astype(float))
    for i in np.argsort(dist):
        sl = da.isel(time=int(i))
        if np.isfinite(sl.values).any():
            return sl.transpose("latitude", "longitude")
    raise ValueError("No valid data. Widen the dates or move the box (it may be mostly land).")


@dataclass
class Sample:
    value: float
    status: str            # "ok" | "fallback" | "land" | "outside"
    bounds: list | None    # [[south, west], [north, east]] of the cell the value comes from
    dist_km: float = 0.0   # distance click -> source cell centre (fallback only)


def sample(da, lat, lon, fallback_cells=0):
    """Value of the Copernicus cell containing (lat, lon).
    outside  = beyond the grid edge (never returns a value from a far-away cell)
    land     = the cell is masked (NaN)
    fallback = the cell was NaN, so the nearest valid cell within `fallback_cells` cells was used."""
    la, lo = da.latitude.values, da.longitude.values
    dlat, dlon = abs(float(la[1] - la[0])), abs(float(lo[1] - lo[0]))
    if not (la.min() - dlat / 2 <= lat <= la.max() + dlat / 2 and
            lo.min() - dlon / 2 <= lon <= lo.max() + dlon / 2):
        return Sample(float("nan"), "outside", None)
    vals = da.values
    i, j = int(np.abs(la - lat).argmin()), int(np.abs(lo - lon).argmin())

    def bounds(a, b):
        return [[float(la[a]) - dlat / 2, float(lo[b]) - dlon / 2],
                [float(la[a]) + dlat / 2, float(lo[b]) + dlon / 2]]

    if np.isfinite(vals[i, j]):
        return Sample(float(vals[i, j]), "ok", bounds(i, j))
    best = None
    k = int(fallback_cells)
    for a in range(max(0, i - k), min(len(la), i + k + 1)):
        for b in range(max(0, j - k), min(len(lo), j + k + 1)):
            if np.isfinite(vals[a, b]):
                d = np.hypot((la[a] - lat) * 111.0, (lo[b] - lon) * 111.0 * np.cos(np.radians(lat)))
                if best is None or d < best[0]:
                    best = (d, a, b)
    if best is None:
        return Sample(float("nan"), "land", None)
    d, a, b = best
    return Sample(float(vals[a, b]), "fallback", bounds(a, b), float(d))


def value_at(da, lat, lon, fallback_cells=0):
    """Number only (the validator will use this). NaN when outside the grid or on land."""
    return sample(da, lat, lon, fallback_cells).value
