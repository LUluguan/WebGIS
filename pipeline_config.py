# -*- coding: utf-8 -*-
"""
pipeline_config.py — 全仓共用的研究区与算法常量(唯一权威定义)。

背景(2026-10 审计): 研究区 bbox 在 7 个文件各写一份、DEPTH_CAP/RUNOFF_COEF 等在
4+ 个文件重复, 改一处漏一处即产生口径分叉。本文件零依赖(不 import numpy/rasterio),
可在任何模块任何时机安全导入。
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# ===== 研究区(珠江新城/广州塔) =====
LON_MIN, LON_MAX = 113.30, 113.34
LAT_MIN, LAT_MAX = 23.09, 23.13
STUDY_BOUNDS = {"west": LON_MIN, "south": LAT_MIN, "east": LON_MAX, "north": LAT_MAX}
STUDY_WS = (LON_MIN, LAT_MIN, LON_MAX, LAT_MAX)    # west, south, east, north
STUDY_LL = (LON_MIN, LON_MAX, LAT_MIN, LAT_MAX)    # lon_min, lon_max, lat_min, lat_max

# ===== 浴缸法 / 色带 / 情景 =====
DEPTH_CAP = 6.0         # 水深色带上限(m), 超出按最深色
DEPTH_THRESH = 0.05     # 淹没判定阈值(m)
RUNOFF_COEF = 0.50      # 综合径流系数(高城市化, 可被请求参数覆盖)

# ===== 2/5/10/50/100 年 24h 设计暴雨后备值(prep_design_storm.py 产物缺失时) =====
RETURNS_FALLBACK = {2: 118.5, 5: 166.3, 10: 198.9, 50: 270.6, 100: 300.6}
