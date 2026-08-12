# -*- coding: utf-8 -*-
"""
train_unet.py — 在 GF-FloodNet 上训练 UNet 水体提取模型(CPU 版, 子集训练)

标签语义: annotation 中 0 与 1 均为水体(二者从不共现, 均为近红外低值), 255 为背景。
  水体 mask = (label < 255)。

用法:
  python train_unet.py --subset 2000 --val 300 --epochs 4 --batch 12 --size 256
"""
import os, glob, json, random, argparse, time
import numpy as np
import tifffile
import torch
import torch.nn as nn
from unet_model import UNet

DATA = r"D:\Competiton\GF-FloodNet\GF-FloodNet-v1"


def load_pairs():
    anns = sorted(glob.glob(os.path.join(DATA, "annotations", "*.tif")))
    pairs = []
    for a in anns:
        name = os.path.basename(a)
        img = os.path.join(DATA, "images", name)
        if os.path.exists(img):
            pairs.append((img, a))
    return pairs


def preload(pairs, size):
    imgs, masks = [], []
    for img, ann in pairs:
        I = tifffile.imread(img)                       # (H, W, 5) uint16
        A = tifffile.imread(ann)                       # (H, W) uint8
        if size != I.shape[0]:
            from PIL import Image
            I = np.stack([np.array(Image.fromarray(I[..., b], 'I;16').resize((size, size)))
                          for b in range(I.shape[2])], axis=2)
            A = np.array(Image.fromarray(A, 'L').resize((size, size)))
        imgs.append(I.transpose(2, 0, 1))              # (5, H, W)
        masks.append((A < 255).astype(np.float32))     # 水体=1
    imgs = np.stack(imgs).astype(np.float32)
    masks = np.stack(masks)[:, None]                   # (N, 1, H, W)
    return imgs, masks


def dice_loss(logits, target, smooth=1.0):
    probs = torch.sigmoid(logits)
    inter = (probs * target).sum()
    return 1.0 - (2 * inter + smooth) / (probs.sum() + target.sum() + smooth)


def metrics(logits, target):
    pred = (torch.sigmoid(logits) > 0.5).float()
    inter = (pred * target).sum().item()
    union = (pred + target).clamp(0, 1).sum().item() + 1e-6
    return inter / union


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subset", type=int, default=2000)
    ap.add_argument("--val", type=int, default=300)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--base", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--outdir", default=r"D:\Competiton\unet_out")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    torch.set_num_threads(18)

    pairs = load_pairs()
    random.Random(0).shuffle(pairs)
    train_pairs = pairs[: args.subset]
    val_pairs = pairs[args.subset: args.subset + args.val]
    print("train %d  val %d  (total %d)" % (len(train_pairs), len(val_pairs), len(pairs)))

    print("预加载训练集...")
    t0 = time.time()
    X, Y = preload(train_pairs, args.size)
    Xv, Yv = preload(val_pairs, args.size)
    print("  加载完成 %.1fs  X %s  Y %s  (RAM约 %.1fGB)" % (
        time.time() - t0, X.shape, Y.shape, (X.nbytes + Y.nbytes) / 1e9))

    mean = [float(X[:, b].mean()) for b in range(X.shape[1])]
    std = [float(X[:, b].std()) + 1e-6 for b in range(X.shape[1])]
    print("per-band mean:", [round(m, 1) for m in mean])
    print("per-band std :", [round(s, 1) for s in std])

    model = UNet(5, 1, base=args.base)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)

    def norm_and_batch(A):
        A = A.copy()
        for b in range(5):
            A[:, b] = (A[:, b] - mean[b]) / std[b]
        return torch.from_numpy(A)

    Y_t = torch.from_numpy(Y)

    n = X.shape[0]
    for ep in range(args.epochs):
        idx = np.random.permutation(n)
        model.train()
        tl = 0.0
        for s in range(0, n, args.batch):
            bi = idx[s: s + args.batch]
            xb = norm_and_batch(X[bi])
            yb = Y_t[bi]
            logits = model(xb)
            loss = nn.functional.binary_cross_entropy_with_logits(logits, yb) + dice_loss(logits, yb)
            opt.zero_grad(); loss.backward(); opt.step()
            tl += loss.item()
        sched.step()

        # 验证
        model.eval()
        viou, vloss = 0.0, 0.0
        with torch.no_grad():
            for s in range(0, Xv.shape[0], args.batch):
                xb = norm_and_batch(Xv[s: s + args.batch])
                yb = torch.from_numpy(Yv[s: s + args.batch])
                lg = model(xb)
                viou += metrics(lg, yb) * xb.shape[0]
                vloss += (nn.functional.binary_cross_entropy_with_logits(lg, yb) + dice_loss(lg, yb)).item() * xb.shape[0]
        viou /= Xv.shape[0]; vloss /= Xv.shape[0]
        print("epoch %d/%d  train_loss=%.4f  val_loss=%.4f  val_IoU=%.4f  (%.0fs)" % (
            ep + 1, args.epochs, tl / (n // args.batch), vloss, viou, time.time() - t0))

    ckpt = os.path.join(args.outdir, "unet_water.pt")
    torch.save({"state_dict": model.state_dict(), "mean": mean, "std": std,
                "base": args.base, "size": args.size}, ckpt)
    print("saved ->", ckpt)


if __name__ == "__main__":
    main()
