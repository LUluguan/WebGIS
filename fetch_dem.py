# -*- coding: utf-8 -*-
"""
fetch_dem.py — 从 Copernicus GLO-30 远程 COG 读取研究区窗口并缓存到本地 GeoTIFF。
GLO-30 为 DSM(含建筑高度), 本脚本只负责取回原始窗口, 建筑剔除在 bathtub_flood.py 里做。
"""
import os
import rasterio
from rasterio.windows import from_bounds

import proj_fix  # noqa: F401  PROJ 冲突修复(须在 import rasterio 之前)

ROOT = os.path.dirname(os.path.abspath(__file__))

URL = "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/Copernicus_DSM_COG_10_N23_00_E113_00_DEM/Copernicus_DSM_COG_10_N23_00_E113_00_DEM.tif"
LON_MIN, LON_MAX = 113.30, 113.34
LAT_MIN, LAT_MAX = 23.09, 23.13
OUT = os.path.join(ROOT, "dem", "study_dem.tif")


def main():
    os.makedirs(os.path.join(ROOT, "dem"), exist_ok=True)
    with rasterio.open(URL) as src:
        w = from_bounds(LON_MIN, LAT_MIN, LON_MAX, LAT_MAX, src.transform)
        w = w.round_offsets().round_lengths()
        a = src.read(1, window=w)
        prof = src.profile.copy()
        prof.update(driver="GTiff", height=w.height, width=w.width,
                    count=1, transform=src.window_transform(w),
                    dtype="float32", nodata=src.nodata,
                    compress="deflate", tiled=True)
        with rasterio.open(OUT, "w", **prof) as dst:
            dst.write(a.astype("float32"), 1)
        print("cached %s  %dx%d  crs=%s  nodata=%s"
              % (OUT, w.width, w.height, src.crs, src.nodata))


if __name__ == "__main__":
    main()
