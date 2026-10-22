import sys
from avra.copernicus.client import fetch

ds = fetch(refresh="--refresh" in sys.argv)
print(ds)
print("days:", str(ds.time.min().values)[:10], "->", str(ds.time.max().values)[:10],
      f"({ds.sizes['time']} steps)")
