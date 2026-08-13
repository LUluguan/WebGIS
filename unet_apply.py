# -*- coding: utf-8 -*-
"""unet_apply.py — 5波段堆栈 -> UNet 水体掩膜 -> 边界水位反演水深。"""
import os
import numpy as np
from PIL import Image
import torch
from unet_model import UNet

CKPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "unet_out", "unet_water.pt")


def _load_model():
    ck = torch.load(CKPT, map_location="cpu")
    model = UNet(5, 1, base=ck["base"])
    model.load_state_dict(ck["state_dict"])
    model.eval()
    return model


def normalize(stack):
    """stack (H,W,5) -> 逐波段 z-score; 返回 (norm, mean, std)。"""
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


def predict_mask(stack, size=128, thr=0.5):
    """stack (H,W,5) -> 水体掩膜 (size,size) uint8 {0,255}; prob>thr。"""
    model = _load_model()
    s = stack.astype("float32").copy()
    # 填充 NaN(每波段用中位数), 避免 NaN 经缩放/BN 传播导致输出全 NaN
    for b in range(5):
        col = s[..., b]
        if np.isnan(col).any():
            fin = np.isfinite(col)
            med = float(np.nanmedian(col[fin])) if fin.any() else 0.0
            col[~fin] = med
            s[..., b] = col
    X = resize_square(s, size)
    X, _, _ = normalize(X)
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
    f = sorted(glob.glob(r"D:\Competiton\GF-FloodNet\GF-FloodNet-v1\images\China_*.tif"))[0]
    m = predict_mask(tifffile.imread(f).astype("float32"), 128)
    print("%s 水体占比=%.1f%%" % (os.path.basename(f), 100 * (m > 0).mean()))
