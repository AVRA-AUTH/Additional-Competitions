import copernicusmarine as cm
from avra import config

ds = cm.open_dataset(dataset_id=config.DATASET_ID)   # lazy, downloads nothing
print(ds)
print("first:", str(ds.time.min().values)[:10])
print("last: ", str(ds.time.max().values)[:10])
