# -*- coding: utf-8 -*-
"""unet_apply.py — 5波段堆栈 -> UNet 水体掩膜 -> 边界水位反演水深。

归一化策略(norm 参数, 默认 "auto", /api/predict 与真实事件管线统一走本函数):
  dataset  : 用训练集统计量(checkpoint 里的 mean/std), 与训练完全一致,
             适用于与训练同源的 GF-2/GF-3 影像;
  instance : 用单影像自身逐波段均值/标准差 —— 跨传感器域适配。GF-FloodNet(GF-2,
             各波段均值约 200-400)与 Sentinel-2 L2A(同波段均值可达数千)量级差约
             一个数量级, 直接用训练统计量会把输入推到十余倍标准差之外令模型失效;
             单影像归一化保留相对亮度结构, 是跨传感器推理的必要域适配手段;
  auto     : 逐波段比较输入均值与训练均值, 任一波段偏离 > 4 倍训练标准差即判定为
             跨域数据自动切换 instance, 否则用 dataset。同一模型、同一策略,
             同源/跨域影像都能得到与各自口径一致的推理结果。
"""
import os
import numpy as np
from PIL import Image
import torch
from unet_model import UNet

ROOT = os.path.dirname(os.path.abspath(__file__))
CKPT = os.path.join(ROOT, "unet_out", "unet_water.pt")

_model_cache = None      # (ckpt_dict, model)
_ckpt_path_used = None


def load_model(ckpt_path=CKPT):
    """加载并缓存模型(进程级单例), 避免 /api/predict 每次请求重读 checkpoint。"""
    global _model_cache, _ckpt_path_used
    if _model_cache is None or _ckpt_path_used != ckpt_path:
        ck = torch.load(ckpt_path, map_location="cpu")
        model = UNet(5, 1, base=ck["base"])
        model.load_state_dict(ck["state_dict"])
        model.eval()
        _model_cache, _ckpt_path_used = (ck, model), ckpt_path
    return _model_cache


def normalize(stack):
    """stack (H,W,5) -> 逐波段 z-score(instance 归一化); 返回 (norm, mean, std)。"""
    s = stack.astype("float32")
    mean = s.reshape(-1, 5).mean(axis=0)
    std = s.reshape(-1, 5).std(axis=0) + 1e-6
    for b in range(5):
        s[..., b] = (s[..., b] - mean[b]) / std[b]
    return s, mean, std


def resize_square(stack, size=128):
    """stack (H,W,5) -> 各波段最近邻缩放到 size×size。"""
    h, w = stack.shape[:2]
    if h == size and w == size:
        return stack.astype("float32")
    bands = [np.array(Image.fromarray(stack[..., b].astype("float32")).resize((size, size)))
             for b in range(5)]
    return np.stack(bands, axis=2).astype("float32")


def _norm_mode(X, ck):
    """auto 判定: 输入逐波段均值偏离训练均值 > 4σ 即视为跨域(Sentinel 等)。"""
    mean = np.asarray(ck["mean"], dtype="float32")
    std = np.asarray(ck["std"], dtype="float32")
    m = X.reshape(-1, X.shape[-1]).mean(axis=0)
    return "instance" if bool((np.abs(m - mean) > 4 * std).any()) else "dataset"


def predict_mask(stack, size=128, thr=0.5, norm="auto", ckpt_path=CKPT):
    """stack (H,W,5) -> 水体掩膜 (size,size) uint8 {0,255}; prob>thr。
    norm: auto/dataset/instance, 见模块 docstring。"""
    ck, model = load_model(ckpt_path)
    s = stack.astype("float32").copy()
    # 填充 NaN(每波段用中位数), 避免 NaN 经缩放/BN 传播导致输出全 NaN
    for b in range(s.shape[2]):
        col = s[..., b]
        if np.isnan(col).any():
            fin = np.isfinite(col)
            med = float(np.nanmedian(col[fin])) if fin.any() else 0.0
            col[~fin] = med
            s[..., b] = col
    X = resize_square(s, size)
    mode = _norm_mode(X, ck) if norm == "auto" else norm
    if mode == "instance":
        X, _, _ = normalize(X)
    else:
        X = X.copy()
        for b in range(5):
            X[..., b] = (X[..., b] - ck["mean"][b]) / ck["std"][b]
    Xt = torch.from_numpy(X.transpose(2, 0, 1)[None])          # (1,5,size,size)
    with torch.no_grad():
        prob = torch.sigmoid(model(Xt))[0, 0].numpy()
    return (prob > thr).astype(np.uint8) * 255


def invert_depth(mask, dem):
    """mask (H,W) bool/uint8, dem (H,W) float -> (W_level, depth)。复用 water_level_inversion.invert。"""
    from water_level_inversion import invert
    m = mask.astype(bool)
    if m.shape != dem.shape:
        m = np.array(Image.fromarray((m * 255).astype("uint8"), "L")
                     .resize((dem.shape[1], dem.shape[0]), Image.NEAREST)) > 0
    return invert(m, dem)


if __name__ == "__main__":
    import tifffile, glob
    f = sorted(glob.glob(os.path.join(ROOT, "GF-FloodNet", "GF-FloodNet-v1",
                                      "images", "China_*.tif")))[0]
    m = predict_mask(tifffile.imread(f).astype("float32"), 128)
    print("%s 水体占比=%.1f%%" % (os.path.basename(f), 100 * (m > 0).mean()))
