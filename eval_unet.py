# -*- coding: utf-8 -*-
"""
eval_unet.py — UNet 水体提取评估: 在验证集上算 IoU, 并生成可视化对比图。
"""
import os, glob, random
import numpy as np
import tifffile
import torch
from PIL import Image
from unet_model import UNet

DATA = r"D:\Competiton\GF-FloodNet\GF-FloodNet-v1"
CKPT = r"D:\Competiton\unet_out\unet_water.pt"
OUT = r"D:\Competiton\unet_out"


def main(n=20):
    ck = torch.load(CKPT, map_location="cpu")
    mean, std, size = ck["mean"], ck["std"], ck["size"]
    model = UNet(5, 1, base=ck["base"]); model.load_state_dict(ck["state_dict"]); model.eval()

    anns = sorted(glob.glob(os.path.join(DATA, "annotations", "*.tif")))
    val = [a for a in anns if a.split("\\")[-1].startswith(("China", "South Africa", "Australia"))][-n:]

    total_inter = 0; total_union = 0; n_water = 0
    for i, a in enumerate(val):
        name = os.path.basename(a)
        img = os.path.join(DATA, "images", name)
        I = tifffile.imread(img).astype(np.float32)
        A = tifffile.imread(a)
        gt = (A < 255).astype(np.uint8)

        if I.shape[0] != size:
            I = np.stack([np.array(Image.fromarray(I[..., b].astype("uint16"), "I;16").resize((size, size)))
                          for b in range(5)], axis=2).astype(np.float32)
            gt = np.array(Image.fromarray((gt * 255).astype("uint8"), "L").resize((size, size))) > 0
        X = I.transpose(2, 0, 1)
        for b in range(5):
            X[b] = (X[b] - mean[b]) / std[b]
        with torch.no_grad():
            prob = torch.sigmoid(model(torch.from_numpy(X[None])))[0, 0].numpy()
        pred = (prob > 0.5).astype(np.uint8)
        total_inter += (pred & gt).sum(); total_union += (pred | gt).sum()
        if gt.sum() > 0:
            n_water += 1

        if i == 0:   # 存第一张可视化对比
            rgb = I[:, :, [2, 1, 0]].astype(np.float32)   # R,G,B
            for c in range(3):
                rgb[:, :, c] = (rgb[:, :, c] - rgb[:, :, c].min()) / (rgb[:, :, c].max() - rgb[:, :, c].min() + 1e-6) * 255
            overlay = np.array(rgb, dtype=np.uint8).copy()
            overlay[pred > 0] = [0, 140, 255]            # 预测水体蓝色
            Image.fromarray(overlay).save(os.path.join(OUT, "demo_pred.png"))
            overlay2 = np.array(rgb, dtype=np.uint8).copy()
            overlay2[gt > 0] = [0, 255, 140]             # 真值水体绿色
            Image.fromarray(overlay2).save(os.path.join(OUT, "demo_gt.png"))
            Image.fromarray(np.array(rgb, dtype=np.uint8)).save(os.path.join(OUT, "demo_img.png"))
            print("保存可视化: demo_img.png / demo_pred.png / demo_gt.png  (样本: %s)" % name)

    print("验证 %d 样本(其中 %d 含水)  总像素 IoU = %.4f" % (
        len(val), n_water, total_inter / max(total_union, 1)))


if __name__ == "__main__":
    main()
