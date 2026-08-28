# -*- coding: utf-8 -*-
"""
fetch_pop.py — 下载研究区 WorldPop 人口格网并重采样到 DTM 网格, 供 /api/impact 统计受影响人口。

数据: WorldPop Global 2000-2020 · 2020 · CHN · 100m (CC-BY 4.0, 免费公开)
      https://data.worldpop.org/GIS/Population/Global_2000_2020/2020/CHN/chn_ppp_2020.tif
注意: 该服务器(实测)不支持 HTTP Range 请求, 无法远程窗口读取——本脚本需先完整下载
      全国文件(约4.6GB)再裁剪; 在有快速网络/代理的环境运行。格网缺失时 /api/impact
      自动退化为「天河区七普人口密度均摊估算」口径(app.py POP_DENSITY), 功能不中断。
用法: python fetch_pop.py   →  产出 dem/study_pop.tif(与 study_dtm.tif 同网格, 单位:人/像元)
"""
import os
import proj_fix  # noqa: F401  PROJ 冲突修复(须在 import rasterio 之前)
import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds as tf_from_bounds

ROOT = os.path.dirname(os.path.abspath(__file__))
POP_URL = ("https://data.worldpop.org/GIS/Population/Global_2000_2020/2020/CHN/"
           "chn_ppp_2020.tif")
OUT = os.path.join(ROOT, "dem", "study_pop.tif")
DTM = os.path.join(ROOT, "dem", "study_dtm.tif")
# 略大于研究区(113.30-113.34 / 23.09-23.13), 防边缘缺
BBOX = (113.28, 23.07, 113.36, 23.15)


def main():
    with rasterio.open(DTM) as dtm_src:
        dtm = dtm_src.read(1)
        dst_transform = dtm_src.transform
        dst_crs = dtm_src.crs
        dst_shape = dtm.shape
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    env = dict(GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="2",
               GDAL_HTTP_TIMEOUT="60", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif")
    with rasterio.Env(**env):
        print("远程窗口读取 WorldPop CHN 2020 (100m) ...")
        with rasterio.open(POP_URL) as src:
            print("  src:", src.width, "x", src.height, src.res, src.crs)
            w = from_bounds(*BBOX, src.transform)
            pop = src.read(1, window=w)
            pop_tr = src.window_transform(w)
            nod = src.nodata
    pop = pop.astype("float32")
    if nod is not None:
        pop[pop == nod] = 0.0
    pop[~np.isfinite(pop)] = 0.0
    print("  窗口", pop.shape, "人口合计=%.0f" % pop.sum())

    # 重采样(双线性)到 DTM 网格
    dst = np.zeros(dst_shape, dtype="float32")
    reproject(pop, dst, src_transform=pop_tr, src_crs=src.crs,
              dst_transform=dst_transform, dst_crs=dst_crs,
              resampling=Resampling.bilinear)
    dst[~np.isfinite(dst)] = 0.0
    with rasterio.open(OUT, "w", driver="GTiff", height=dst_shape[0],
                       width=dst_shape[1], count=1, dtype="float32",
                       crs=dst_crs, transform=dst_transform, nodata=-1.0) as out:
        out.write(dst, 1)
    print("saved ->", OUT, " 研究区网格人口合计=%.0f" % dst.sum())


if __name__ == "__main__":
    main()
