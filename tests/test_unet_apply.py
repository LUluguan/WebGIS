# -*- coding: utf-8 -*-
import os, sys, glob, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import unet_apply

def test_normalize():
    rng = np.random.default_rng(0)
    stack = rng.normal(loc=[0, 10, 20, 30, 40], scale=[5, 8, 11, 14, 17],
                       size=(4, 4, 5)).astype("float32")
    n, mean, std = unet_apply.normalize(stack)
    assert n.shape == (4, 4, 5)
    assert np.allclose(n.mean(axis=(0, 1)), 0, atol=1e-3), "归一化后均值应为0"
    assert np.allclose(n.std(axis=(0, 1)), 1, atol=1e-3), "归一化后标准差应为1"
    assert mean.shape == (5,) and std.shape == (5,)

def test_resize_square():
    ys, xs = np.meshgrid(np.linspace(0, 10, 64), np.linspace(0, 10, 64))
    base = (np.sin(xs) + np.cos(ys)).astype("float32")           # 平滑低频信号
    s = np.stack([base * (b + 1) for b in range(5)], axis=2).astype("float32")
    r = unet_apply.resize_square(s, 128)
    assert r.shape == (128, 128, 5)
    assert np.abs(r[::2, ::2] - s).mean() < 0.1, "放大后低频应近似保留"

def test_predict_on_flood_sample():
    import tifffile
    files = sorted(glob.glob(os.path.join(ROOT, "GF-FloodNet", "GF-FloodNet-v1", "images", "China_*.tif")))
    assert files, "无 GF-FloodNet 中国样本"
    # 选第一个"部分含水"瓦片(真值水体 5%~95%)作样本; 排序最前的 China_016_* 是
    # 全水域退化瓦片(标注 100% 为水体), 不能作为"应检出部分水体"的断言对象。
    picked = None
    for f in files:
        A = tifffile.imread(f.replace("images", "annotations"))
        tf = (A < 255).mean()
        if 0.05 <= tf <= 0.95:
            picked = (f, tf)
            break
    assert picked, "未找到部分含水的中国样本瓦片"
    f, tf = picked
    I = tifffile.imread(f).astype("float32")          # (256,256,5) uint16
    mask = unet_apply.predict_mask(I, 128)
    frac = (mask > 0).mean()
    assert 0.005 < frac < 0.995, "部分含水样本应检出水体, 真值 %.1f%%, 预测 %.1f%%" % (100 * tf, 100 * frac)
    print("样本 %s 真值=%.1f%% 预测水体占比=%.1f%%" % (os.path.basename(f), 100 * tf, 100 * frac))

if __name__ == "__main__":
    test_normalize(); test_resize_square(); test_predict_on_flood_sample()
    print("test_unet_apply OK")
