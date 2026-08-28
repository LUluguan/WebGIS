# -*- coding: utf-8 -*-
"""proj_fix.py — rasterio PROJ 数据路径冲突修复(须在 import rasterio 之前导入)。

问题: 系统 PROJ_LIB 可能指向 PostGIS 自带的旧版 proj.db, 令 rasterio 报
"PROJ: proj_create_from_database ..." 错误。
做法: 不触发 rasterio 初始化, 直接按 sys.path 定位 rasterio 自带的 proj_data 目录,
覆盖 PROJ_LIB/PROJ_DATA; 定位不到或本就指向 rasterio 时不做任何事(跨机器安全)。
"""
import os
import sys

_cur = os.environ.get("PROJ_LIB", "")
if "rasterio" not in _cur.lower():
    for _sp in list(sys.path):
        if not _sp or not os.path.isdir(_sp):
            continue
        _cand = os.path.join(_sp, "rasterio", "proj_data")
        if os.path.isdir(_cand):
            os.environ["PROJ_LIB"] = _cand
            os.environ["PROJ_DATA"] = _cand
            break
