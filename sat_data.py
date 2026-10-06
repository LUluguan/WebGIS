# -*- coding: utf-8 -*-
"""sat_data.py — 卫星数据获取: Planetary Computer STAC 检索 + 匿名签名 + 窗口读取重投影。"""
import proj_fix  # noqa: F401  PROJ 冲突修复(须在 import rasterio 之前)
import numpy as np
import rasterio
from rasterio.warp import transform_bounds, reproject, Resampling
from rasterio.windows import from_bounds
from rasterio.transform import from_bounds as tf_from_bounds
from rasterio.transform import Affine

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"


def stac_search(collection, bbox, datetime, limit=5, sortby=None):
    import requests
    payload = {"collections": [collection], "bbox": list(bbox),
               "datetime": datetime, "limit": limit}
    if sortby:
        payload["sortby"] = [{"field": sortby, "direction": "asc"}]
    r = requests.post(STAC, json=payload, timeout=60)
    r.raise_for_status()
    return r.json().get("features", [])


def sign_url(href, retries=4):
    """PC 匿名签名(限流/网关瞬断退避重试: 5/15/30/60s)。429 与 5xx 均为暂时性错误。"""
    import requests
    import time
    last = None
    for i in range(retries):
        r = requests.get(SIGN, params={"href": href}, timeout=60)
        if r.status_code == 429 or r.status_code in (500, 502, 503, 504):
            wait = (5, 15, 30, 60)[min(i, 3)]
            time.sleep(wait)
            last = r
            continue
        r.raise_for_status()
        return r.json()["href"]
    last.raise_for_status()
    raise RuntimeError("签名接口持续不可用(最后 HTTP %d)" % last.status_code)


def lonlat_bbox_to_grid(bbox, dst_epsg, res_m):
    """lonlat bbox -> 覆盖该框的 dst_epsg 网格 (width, height, dst_transform)。"""
    dst_crs = rasterio.crs.CRS.from_epsg(dst_epsg)
    left, bottom, right, top = transform_bounds(
        rasterio.crs.CRS.from_epsg(4326), dst_crs, *bbox)
    width = max(1, int(round((right - left) / res_m)))
    height = max(1, int(round((top - bottom) / res_m)))
    dst_transform = tf_from_bounds(left, bottom, right, top, width, height)
    return width, height, dst_transform


def read_window(href, bbox, dst_epsg, out_width, out_height, resampling=None):
    """读取 href 第1波段, 裁剪到 lon/lat bbox, 重投影到 dst_epsg 网格 (out_height, out_width)。
    resampling: 默认 bilinear(连续量); 分类波段(如 SCL)必须传 NEAREST,
    否则插值产生非整型类码, 云占比被系统性少算。"""
    dst_crs = rasterio.crs.CRS.from_epsg(dst_epsg)
    db = transform_bounds(rasterio.crs.CRS.from_epsg(4326), dst_crs, *bbox)
    dst_transform = tf_from_bounds(*db, out_width, out_height)
    with rasterio.open(href) as src:
        sb = transform_bounds(dst_crs, src.crs, *db)
        win = from_bounds(*sb, src.transform).round_offsets().round_lengths()
        if win.width < 1 or win.height < 1:
            raise ValueError("bbox 与栅格无交集: %s" % href)
        d = src.read(1, window=win, boundless=True).astype("float32")
        if src.nodata is not None:
            d = np.where(d == src.nodata, -9999.0, d)
        src_tf = src.transform * Affine.translation(win.col_off, win.row_off)
        out = np.full((out_height, out_width), np.nan, dtype="float32")
        reproject(d, out, src_transform=src_tf, src_crs=src.crs,
                  src_nodata=-9999.0, dst_transform=dst_transform, dst_crs=dst_crs,
                  # 注意 Resampling.nearest 是 IntEnum 值 0, 用 `or` 会被吞回 bilinear
                  resampling=resampling if resampling is not None else Resampling.bilinear,
                  dst_nodata=np.nan)
        return out
