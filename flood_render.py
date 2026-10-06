# -*- coding: utf-8 -*-
"""
flood_render.py — 水深渲染共享实现(色带 + 淹没掩膜), 唯一权威。

背景(2026-10 审计 ARC-01): colorize 曾在 app.py 与 export_web.py 双份实现,
数值恰好一致, 但改一处漏一处即产生交付 PNG 与接口出图口径分叉。
服务层(app.py)、导出脚本(export_web.py)一律 import 本模块, 不再各写一份。
"""
import numpy as np
from PIL import Image

from pipeline_config import DEPTH_CAP, DEPTH_THRESH


def colorize_depth(depth, land=None):
    """水深 → RGBA uint8 数组(浅蓝→深蓝, alpha=200)。
    land: 布尔掩膜(True=陆域)。提供时仅陆域淹没着色, 常年珠江河道透明——
    与专题图/淹没范围"仅陆域"口径一致(旧版河道被当淹没, 占着色格 56–73%)。"""
    h, w = depth.shape
    img = np.zeros((h, w, 4), dtype=np.uint8)
    mask = depth > DEPTH_THRESH
    if land is not None:
        mask = mask & land
    t = np.clip(depth / DEPTH_CAP, 0.0, 1.0)
    img[..., 0] = (166.0 * (1 - t)).astype("uint8")
    img[..., 1] = (227.0 - 176.0 * t).astype("uint8")
    img[..., 2] = (255.0 - 153.0 * t).astype("uint8")
    img[..., 3] = np.where(mask, 200, 0).astype("uint8")
    return img


def colorize(depth, land=None):
    """水深 → PIL RGBA 色带图(服务层接口出图用)。"""
    return Image.fromarray(colorize_depth(depth, land), "RGBA")
