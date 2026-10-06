# -*- coding: utf-8 -*-
"""
pipeline_config.py — 全仓共用的研究区与算法常量(唯一权威定义)。

背景(2026-10 审计): 研究区 bbox 在 7 个文件各写一份、DEPTH_CAP/RUNOFF_COEF 等在
4+ 个文件重复, 改一处漏一处即产生口径分叉。本文件零第三方依赖(不 import numpy/rasterio,
cell_area_m2 只用 math + affine 属性), 可在任何模块任何时机安全导入。
"""
import math
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

# ===== 重现期档位(全仓唯一权威列表) + 24h 设计暴雨后备值 =====
RETURNS = [2, 5, 10, 50, 100]

# ===== 2/5/10/50/100 年 24h 设计暴雨后备值(prep_design_storm.py 产物缺失时) =====
RETURNS_FALLBACK = {2: 118.5, 5: 166.3, 10: 198.9, 50: 270.6, 100: 300.6}

# ===== 面积/距离换算(唯一权威; NUM-01: 纬度取研究区中心, 不同纬度取值差异 ≤0.7%) =====
M_PER_DEG_LON_EQUATOR = 111320.0   # 赤道每经度米数
M_PER_DEG_LAT = 110574.0           # 每纬度米数(中纬度近似)
CELL_LAT = 23.11                   # 面积换算固定纬度(研究区中心)


def cell_area_m2(transform, lat=CELL_LAT):
    """单像元实地面积(m²)。transform 为 affine 对象(a=经度像元宽, e=纬度像元高)。"""
    return (transform.a * M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat))) * (
        abs(transform.e) * M_PER_DEG_LAT)


# ===== 水深分级直方分界(唯一权威; depth_hist/export_dashboard 共用, 下缘=淹没阈值) =====
DEPTH_BINS = ((DEPTH_THRESH, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 1e9))
