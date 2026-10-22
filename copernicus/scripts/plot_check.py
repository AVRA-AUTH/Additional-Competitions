import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from avra import config
from avra.copernicus.client import fetch, latest_field, value_at

da = latest_field(fetch())
print("time:", str(da.time.values)[:10])
print("grid:", da.sizes["latitude"], "x", da.sizes["longitude"], "cells")
print("cell size (deg):", float(abs(da.latitude[1] - da.latitude[0])))
print("NaN fraction:", float(np.isnan(da.values).mean()))
print("range:", float(np.nanmin(da.values)), float(np.nanmax(da.values)))

lat = float(da.latitude.mean()); lon = float(da.longitude.mean())
print("value_at centre:", value_at(da, lat, lon))

da.plot(norm=LogNorm(config.VMIN, config.VMAX), cmap=config.CMAP)
plt.savefig("check.png", dpi=120)
print("saved check.png")
