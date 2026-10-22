"""What is actually in the cache: per layer and a few days, NaN fraction and value spread."""
import numpy as np
from avra.copernicus.client import fetch, days, field_at

ds = fetch()
d = days(ds)
print("days:", d[0], "->", d[-1], f"({len(d)} steps)   grid:",
      ds.sizes["latitude"], "x", ds.sizes["longitude"])
for v in ds.data_vars:
    print(f"\n{v}")
    for i in sorted({0, len(d) // 2, len(d) - 1}):
        a = field_at(ds, v, i).values
        ok = a[np.isfinite(a)]
        p = np.percentile(ok, [2, 50, 98]) if ok.size else [np.nan] * 3
        print(f"  {d[i]}  NaN {np.isnan(a).mean():.2f}  min {ok.min():.4f}  p2 {p[0]:.4f}  "
              f"median {p[1]:.4f}  p98 {p[2]:.4f}  max {ok.max():.4f}")
