# -*- coding: utf-8 -*-
"""
bathtub_flood.py — 浴缸法 + 水位反演 (P0b, Route A 的重现期情景)

数据流:
  1. DEM: Copernicus GLO-30 研究区窗口(dem/study_dem.tif, 30m, 4326) — DSM(含建筑)
  2. 建筑剔除: gz_tower_buildings 的 290 栋 footprint 栅格化 -> 建筑格网邻域插值回地面
  3. 陆域体积守恒(浴缸): 仅对陆域(z>0)求水面高程 W, 使陆域平均水深 = 径流深 Q
     => 均值 max(0, W - z) 于 z>0 = Q ;  河道(z<=0)为常年水面, 不参与注水平衡
  4. 水深 D = max(0, W - z) 全域输出(河道自然更深);  新淹没范围 = z>0 且 D>threshold
     W 即「反演水位」, 水深 = 水位 - 地形, 与文档表述一致

径流深 Q = R/1000 × C,  C=综合径流系数(高城市化下垫面, 含下渗/排水/短历时折减, 可调)。

输出: flood_out/flood_depth_{T}y.tif (水深, m), flood_out/flood_extent_{T}y.geojson (新淹没陆地),
      flood_out/scenarios.json
"""
import os, json
import proj_fix  # noqa: F401  PROJ 冲突修复(须在 import rasterio 之前)

ROOT = os.path.dirname(os.path.abspath(__file__))
import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from numpy.lib.stride_tricks import sliding_window_view

# 研究区(珠江新城/广州塔)
LON_MIN, LON_MAX = 113.30, 113.34
LAT_MIN, LAT_MAX = 23.09, 23.13

DEM_PATH = os.path.join(ROOT, "dem", "study_dem.tif")
BLD_PATH = os.path.join(ROOT, "gz_tower_buildings.geojson")
OUTDIR = os.path.join(ROOT, "flood_out")

# 重现期 -> 24h 设计暴雨(mm): 皮尔逊III型(权威), 来自 prep_design_storm.py
# 依据《广东省暴雨径流查算图表》/《广东省暴雨参数等值线图》(2003) 广州参数: H24=130, Cv=0.4, Cs=3.5Cv
STORM_JSON = os.path.join(ROOT, "flood_out", "design_storm_24h.json")
if os.path.exists(STORM_JSON):
    with open(STORM_JSON, encoding="utf-8") as _f:
        RETURNS = {int(_k): float(_v) for _k, _v in json.load(_f).items()}
else:
    RETURNS = {2: 118.5, 5: 166.3, 10: 198.9, 50: 270.6, 100: 300.6}

RUNOFF_COEF = 0.50    # 综合径流系数(高城市化, 可调)
DEPTH_THRESH = 0.05   # 淹没判定阈值 (m)
RIVER_FLOOR = -15.0   # 河道噪声下限(保留真实负值河道, 仅剔极端离群)
TIF_NODATA = -9999.0


def load_dem():
    with rasterio.open(DEM_PATH) as src:
        z = src.read(1).astype("float32")
        transform = src.transform
    z[np.isnan(z)] = 0.0
    print("DEM %dx%d  elev min=%.1f max=%.1f median=%.1f m"
          % (z.shape[1], z.shape[0], float(z.min()), float(z.max()), float(np.median(z))))
    return z, transform


def remove_buildings(z, transform):
    """用建筑 footprint 把 DSM 里的建筑格网插值回地面(近似 DTM)。"""
    with open(BLD_PATH, encoding="utf-8") as f:
        gj = json.load(f)
    geoms = [(feat["geometry"], 1) for feat in gj["features"] if feat.get("geometry")]
    if not geoms:
        return z
    bmask = rasterize(geoms, out_shape=z.shape, transform=transform, fill=0, dtype="uint8")
    nbld = int(bmask.sum())
    if nbld == 0:
        print("  no building cells rasterized")
        return z
    a = z.copy()
    a[bmask > 0] = np.nan
    nanmask = np.isnan(a)
    iters = 0
    while nanmask.any() and iters < 500:
        pad = np.pad(a, 1, mode="constant", constant_values=np.nan)
        w = sliding_window_view(pad, (3, 3)).reshape(a.shape[0], a.shape[1], 9)
        means = np.nanmean(w, axis=2)
        a[nanmask] = means[nanmask]
        nanmask = np.isnan(a)
        iters += 1
    print("  building cells removed: %d (%.1f%%)  inpaint iters=%d"
          % (nbld, 100.0 * nbld / z.size, iters))
    return a.astype("float32")


def bathtub(z, q):
    """陆域体积守恒: 求水面高程 W 使陆域(z>0)平均水深 = q(米)。返回 (W, depth)。"""
    land = z > 0
    lo, hi = 0.0, float(z.max())
    for _ in range(60):
        W = 0.5 * (lo + hi)
        if float(np.clip(W - z[land], 0, None).mean()) < q:
            lo = W
        else:
            hi = W
    W = 0.5 * (lo + hi)
    depth = np.clip(W - z, 0, None)
    return W, depth.astype("float32")


def cell_area_m2(transform, lat=23.11):
    import math
    return (transform.a * 111320.0 * math.cos(math.radians(lat))) * (abs(transform.e) * 110574.0)


def polygonize(mask, transform):
    return [g for g, v in shapes(mask.astype("uint8"), transform=transform) if v == 1]


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    z, transform = load_dem()
    z = remove_buildings(z, transform)
    z = np.clip(z, RIVER_FLOOR, None)          # 保留负值河道, 仅剔极端离群

    # 保存裸地 DTM(供前端建筑按真实地面高程抬升)
    dtm_path = os.path.join(ROOT, "dem", "study_dtm.tif")
    with rasterio.open(dtm_path, "w", driver="GTiff", height=z.shape[0],
                       width=z.shape[1], count=1, dtype="float32",
                       crs="EPSG:4326", transform=transform,
                       nodata=TIF_NODATA) as dst:
        dst.write(z, 1)
    print("saved DTM -> %s" % dtm_path)

    land = z > 0
    area = cell_area_m2(transform)
    scenarios = []

    for T, R in RETURNS.items():
        q = R / 1000.0 * RUNOFF_COEF          # 径流深(米)
        W, depth = bathtub(z, q)

        flooded = (depth > DEPTH_THRESH) & land   # 新淹没陆地(不含常年河道)
        area_km2 = float(flooded.sum() * area) / 1e6

        tif_path = os.path.join(OUTDIR, "flood_depth_%dy.tif" % T)
        with rasterio.open(tif_path, "w", driver="GTiff", height=depth.shape[0],
                           width=depth.shape[1], count=1, dtype="float32",
                           crs="EPSG:4326", transform=transform,
                           nodata=TIF_NODATA) as dst:
            dst.write(depth, 1)

        polys = polygonize(flooded, transform)
        gj = {"type": "FeatureCollection",
              "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
              "features": [{"type": "Feature", "id": i, "geometry": p,
                            "properties": {}} for i, p in enumerate(polys)]}
        with open(os.path.join(OUTDIR, "flood_extent_%dy.geojson" % T), "w",
                  encoding="utf-8") as f:
            json.dump(gj, f)

        scen = {"return_period_y": T, "rain_mm": R,
                "runoff_depth_m": round(q, 4),
                "water_level_m": round(float(W), 2),
                "max_depth_m": round(float(depth[flooded].max()), 2),
                "mean_depth_m": round(float(depth[flooded].mean()), 2),
                "flooded_area_km2": round(area_km2, 3),
                "flooded_cells": int(flooded.sum()),
                "river_cells": int((z <= 0).sum())}
        scenarios.append(scen)
        print("T=%3dy R=%5.1fmm -> W=%.2fm  landFlood=%.1f%%(%.3fkm2) maxD=%.2fm meanD=%.2fm"
              % (T, R, W, 100.0 * flooded.sum() / land.sum(), area_km2,
                 depth[flooded].max(), depth[flooded].mean()))

    with open(os.path.join(OUTDIR, "scenarios.json"), "w", encoding="utf-8") as f:
        json.dump(scenarios, f, ensure_ascii=False, indent=2)
    print("\nwrote %d 组 flood_depth/flood_extent + scenarios.json -> %s"
          % (len(scenarios), OUTDIR))


if __name__ == "__main__":
    main()
