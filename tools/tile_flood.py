# -*- coding: utf-8 -*-
"""
tile_flood.py — 淹没水深栅格 → XYZ 瓦片(预渲染切片服务)

参考 NOAA FIM 静态淹没服务 / Copernicus GeoTIFF 分发的"结果切片化"做法:
前端按 z/x/y 请求预渲染瓦片, 替代全量 GeoJSON/大 PNG 下发, 传输量小、缩放流畅。

- 源: flood_out/flood_depth_{T}y.tif (EPSG:4326)
- 目标: flood_out/flood_tiles/{T}/{z}/{x}/{y}.png (EPSG:3857 XYZ 协议, 256px, RGBA)
- 色带与 app.py colorize() 一致(浅蓝→深蓝, DEPTH_CAP=6m, alpha=200, 0.05m 以下透明)
- 重投影: 本脚本内置 4326→3857 解析公式 + 双线性采样(纯 numpy),
  不依赖 GDAL warp(规避机器上 PROJ/PostGIS proj.db 版本冲突)
- 层级: z9(概览) ~ z14(约2.4m/像元, 超采样于 30m 源)

运行: python tools/tile_flood.py
"""
import json, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import rasterio
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTBASE = os.path.join(ROOT, "flood_out", "flood_tiles")
BOUNDS = (113.30, 23.09, 113.34, 23.13)     # 研究区(与 bathtub_flood.py 一致)
DEPTH_CAP = 6.0
DEPTH_THRESH = 0.05
ZOOMS = range(9, 15)
TILE = 256


def lonlat_to_xyz(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def tile_bounds(x, y, z):
    n = 2 ** z
    lon_w = x / n * 360.0 - 180.0
    lon_e = (x + 1) / n * 360.0 - 180.0
    lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_s = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon_w, lat_s, lon_e, lat_n


def sample_tile(src, src_transform, x, y, z):
    """墨卡托瓦片 256×256 双线性采样自 4326 源栅格。纯解析公式, 无 GDAL。"""
    lon_w, lat_s, lon_e, lat_n = tile_bounds(x, y, z)
    n = 2 ** z
    # 像元中心经度线性; 纬度经墨卡托纵距线性插值后反解
    xi = (np.arange(TILE) + 0.5) / TILE
    lon = lon_w + xi * (lon_e - lon_w)
    ym_n = math.asinh(math.tan(math.radians(lat_n)))
    ym_s = math.asinh(math.tan(math.radians(lat_s)))
    yi = (np.arange(TILE) + 0.5) / TILE
    ym = ym_n + yi * (ym_s - ym_n)
    lat = np.degrees(np.arctan(np.sinh(ym)))
    lon_g, lat_g = np.meshgrid(lon, lat)

    # 源栅格连续行列号(Affine 无旋转: col=(lon-c)/a, row=(lat-f)/e)
    col = (lon_g - src_transform.c) / src_transform.a - 0.5
    row = (lat_g - src_transform.f) / src_transform.e - 0.5
    r0 = np.floor(row).astype("int32"); c0 = np.floor(col).astype("int32")
    fr = row - r0; fc = col - c0
    r0c = np.clip(r0, 0, src.shape[0] - 2); c0c = np.clip(c0, 0, src.shape[1] - 2)
    r1c = r0c + 1; c1c = c0c + 1
    inside = ((row >= -0.5) & (row < src.shape[0] - 0.5) &
              (col >= -0.5) & (col < src.shape[1] - 0.5))
    v = (src[r0c, c0c] * (1 - fr) * (1 - fc) + src[r1c, c0c] * fr * (1 - fc) +
         src[r0c, c1c] * (1 - fr) * fc + src[r1c, c1c] * fr * fc)
    return np.where(inside, v, 0.0).astype("float32")


def colorize(depth):
    """与 app.py colorize() 同一色带: 浅蓝→深蓝, 陆地淹没 alpha=200, 其余透明。"""
    t = np.clip(depth / DEPTH_CAP, 0.0, 1.0)
    img = np.zeros((depth.shape[0], depth.shape[1], 4), dtype=np.uint8)
    img[..., 0] = (166.0 * (1 - t)).astype("uint8")
    img[..., 1] = (227.0 - 176.0 * t).astype("uint8")
    img[..., 2] = (255.0 - 153.0 * t).astype("uint8")
    img[..., 3] = np.where(depth > DEPTH_THRESH, 200, 0).astype("uint8")
    return img


def main():
    os.makedirs(OUTBASE, exist_ok=True)
    n_total = 0
    meta = {"generated_by": "tools/tile_flood.py", "zooms": list(ZOOMS),
            "tile_size": TILE, "depth_cap_m": DEPTH_CAP,
            "color": "与 app.py colorize 一致", "scheme": "XYZ/EPSG3857",
            "scenarios": {}}
    for T in (2, 5, 10, 50, 100):
        src_path = os.path.join(ROOT, "flood_out", "flood_depth_%dy.tif" % T)
        if not os.path.exists(src_path):
            print("skip T=%dy (no tif)" % T)
            continue
        with rasterio.open(src_path) as src:
            arr = src.read(1).astype("float32")
            src_transform = src.transform
        arr[~np.isfinite(arr)] = 0.0
        arr[arr < 0] = 0.0

        outdir = os.path.join(OUTBASE, str(T))
        n_scen = 0
        for z in ZOOMS:
            x0, y1 = lonlat_to_xyz(BOUNDS[0], BOUNDS[1], z)
            x1, y0 = lonlat_to_xyz(BOUNDS[2], BOUNDS[3], z)
            for x in range(min(x0, x1), max(x0, x1) + 1):
                for y in range(min(y0, y1), max(y0, y1) + 1):
                    depth = sample_tile(arr, src_transform, x, y, z)
                    if float(depth.max()) <= DEPTH_THRESH:
                        continue                       # 干瓦片不落盘(前端 404 即透明)
                    img = colorize(depth)
                    d = os.path.join(outdir, str(z), str(x))
                    os.makedirs(d, exist_ok=True)
                    Image.fromarray(img, "RGBA").save(os.path.join(d, "%d.png" % y))
                    n_scen += 1
        meta["scenarios"][str(T)] = {"tiles": n_scen}
        n_total += n_scen
        print("T=%dy: %d tiles" % (T, n_scen))

    with open(os.path.join(OUTBASE, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("total %d tiles -> %s" % (n_total, OUTBASE))


if __name__ == "__main__":
    main()
