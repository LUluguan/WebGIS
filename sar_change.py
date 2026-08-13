# -*- coding: utf-8 -*-
"""sar_change.py — Sentinel-1 RTC VV 双时相变化检测: 后向散射骤降 = 洪水新增水面。"""
import numpy as np


def to_db(arr, linear_thresh=1.0):
    """若数组取值呈线性幅度(中位数>1), 转 dB; 否则原样(dB)。"""
    v = arr[np.isfinite(arr)]
    if v.size == 0:
        return arr
    if np.nanmedian(v) > linear_thresh:
        with np.errstate(divide="ignore"):
            return 10.0 * np.log10(np.clip(arr, 1e-8, None))
    return arr


def change_mask(vv_flood, vv_base, drop_db=-8.0):
    """vv_flood 与 vv_base 同为 RTC VV(线性或dB)。返回 True=新增水面(后向散射骤降)。"""
    f = to_db(vv_flood.astype("float32"))
    b = to_db(vv_base.astype("float32"))
    valid = np.isfinite(f) & np.isfinite(b)
    diff = np.full(f.shape, np.nan, dtype="float32")
    diff[valid] = f[valid] - b[valid]
    return (diff < drop_db) & valid
