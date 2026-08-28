# -*- coding: utf-8 -*-
"""
train_unet.py — 在 GF-FloodNet 上训练 UNet 水体提取模型(CPU 版, 子集训练)

标签语义: annotation 中 0 与 1 均为水体(二者从不共现, 均为近红外低值), 255 为背景。
  水体 mask = (label < 255)。

验证集划分(--split):
  region(默认, 推荐): 按场景(scene = 区域_景号, 共19景)做空间划分——同一景的瓦片
    不会同时出现在训练/验证集, 避免随机划分的空间泄漏; 每 epoch 输出分区 IoU。
  random: 旧版随机打散划分(仅作对比, 有空间泄漏, IoU 偏高)。

用法:
  python train_unet.py --subset 2000 --epochs 4 --batch 12 --size 128 --split region
"""
import os, glob, json, random, argparse, time, collections
import numpy as np
import tifffile
import torch
import torch.nn as nn
from unet_model import UNet

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "GF-FloodNet", "GF-FloodNet-v1")


def load_pairs():
    anns = sorted(glob.glob(os.path.join(DATA, "annotations", "*.tif")))
    pairs = []
    for a in anns:
        name = os.path.basename(a)
        img = os.path.join(DATA, "images", name)
        if os.path.exists(img):
            pairs.append((img, a))
    return pairs


def scene_of(path):
    """场景键: 'China_034_10_5.tif' -> 'China_034'(带空格区域名同样适用)。"""
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    parts = stem.split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else stem


def region_of(path):
    return os.path.basename(path).split("_")[0]


def split_region(pairs, val_frac=0.15, seed=0):
    """场景级空间划分: 打散场景后贪心收集, 验证集瓦片数 >= val_frac*总数;
    保证每个大区至少留一景在训练集。返回 (train, val, val_scenes)。"""
    scenes = collections.defaultdict(list)
    for p in pairs:
        scenes[scene_of(p[0])].append(p)
    scene_list = sorted(scenes.keys())
    random.Random(seed).shuffle(scene_list)
    total = len(pairs)
    target = int(total * val_frac)
    region_count = collections.Counter(region_of(p[0]) for p in pairs)
    val, val_tiles = [], 0
    used = set()
    for sc in scene_list:
        if val_tiles >= target:
            break
        reg = region_of(scenes[sc][0][0])
        # 该大区在训练集中只剩这一景时, 不能整区拿走
        if region_count[reg] - len(scenes[sc]) < 1:
            continue
        val.extend(scenes[sc])
        used.add(sc)
        val_tiles += len(scenes[sc])
        region_count[reg] -= len(scenes[sc])
    train = [p for p in pairs if scene_of(p[0]) not in used]
    return train, val, sorted(used)


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
    return inter, union


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subset", type=int, default=2000)
    ap.add_argument("--val", type=int, default=300, help="random 模式验证瓦片数; region 模式验证占比=max(15%%, val/total)")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--base", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--split", choices=["region", "random"], default="region")
    ap.add_argument("--aug", action="store_true", default=True, help="随机水平/垂直翻转增广")
    ap.add_argument("--outdir", default=os.path.join(ROOT, "unet_out"))
    ap.add_argument("--ckpt-name", default="unet_water.pt")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    threads = min(18, os.cpu_count() or 8)
    torch.set_num_threads(threads)

    pairs = load_pairs()
    random.Random(0).shuffle(pairs)
    meta = {"split": args.split}
    if args.split == "region":
        pool = pairs[: max(args.subset * 3, args.subset + 500)]   # 场景级取样池(保证验证场景充足)
        train_pairs, val_pairs, val_scenes = split_region(pool, val_frac=0.15)
        train_pairs = train_pairs[: args.subset]
        meta["val_scenes"] = val_scenes
        print("region split: train %d  val %d  val_scenes=%s" % (len(train_pairs), len(val_pairs), val_scenes))
    else:
        train_pairs = pairs[: args.subset]
        val_pairs = pairs[args.subset: args.subset + args.val]
        print("random split: train %d  val %d  (total %d)" % (len(train_pairs), len(val_pairs), len(pairs)))
    val_regions = [region_of(p[0]) for p in val_pairs]

    print("预加载训练集...")
    t0 = time.time()
    X, Y = preload(train_pairs, args.size)
    Xv, Yv = preload(val_pairs, args.size)
    print("  加载完成 %.1fs  X %s  Y %s  (RAM约 %.1fGB)" % (
        time.time() - t0, X.shape, Y.shape, (X.nbytes + Yv.nbytes) / 1e9))

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
    Yv_t = torch.from_numpy(Yv)

    n = X.shape[0]
    history = []
    for ep in range(args.epochs):
        idx = np.random.permutation(n)
        model.train()
        tl = 0.0
        for s in range(0, n, args.batch):
            bi = idx[s: s + args.batch]
            xb = norm_and_batch(X[bi])
            yb = Y_t[bi]
            if args.aug:                     # 随机水平/垂直翻转(影像与标签同变换)
                if random.random() < 0.5:
                    xb, yb = torch.flip(xb, [3]), torch.flip(yb, [3])
                if random.random() < 0.5:
                    xb, yb = torch.flip(xb, [2]), torch.flip(yb, [2])
            logits = model(xb)
            loss = nn.functional.binary_cross_entropy_with_logits(logits, yb) + dice_loss(logits, yb)
            opt.zero_grad(); loss.backward(); opt.step()
            tl += loss.item()
        sched.step()

        # 验证(总交/总并口径, 同时输出分区 IoU)
        model.eval()
        vinter = vunion = 0.0
        vloss = 0.0
        reg_io = collections.defaultdict(lambda: [0.0, 0.0])
        with torch.no_grad():
            for s in range(0, Xv.shape[0], args.batch):
                xb = norm_and_batch(Xv[s: s + args.batch])
                yb = Yv_t[s: s + args.batch]
                lg = model(xb)
                vloss += (nn.functional.binary_cross_entropy_with_logits(lg, yb) + dice_loss(lg, yb)).item() * xb.shape[0]
                for k in range(xb.shape[0]):
                    i_, u_ = metrics(lg[k: k + 1], yb[k: k + 1])
                    vinter += i_; vunion += u_
                    r = val_regions[s + k]
                    reg_io[r][0] += i_; reg_io[r][1] += u_
        viou = vinter / max(vunion, 1e-6)
        vloss /= Xv.shape[0]
        reg_iou = {r: round(v[0] / max(v[1], 1e-6), 3) for r, v in sorted(reg_io.items())}
        history.append({"epoch": ep + 1, "val_iou": round(viou, 4)})
        print("epoch %d/%d  train_loss=%.4f  val_loss=%.4f  val_IoU=%.4f  (%.0fs)" % (
            ep + 1, args.epochs, tl / (n // args.batch), vloss, viou, time.time() - t0))
        print("  分区IoU:", reg_iou)

    ckpt = os.path.join(args.outdir, args.ckpt_name)
    torch.save({"state_dict": model.state_dict(), "mean": mean, "std": std,
                "base": args.base, "size": args.size,
                "val_iou": round(viou, 4), "meta": meta,
                "history": history}, ckpt)
    with open(os.path.join(args.outdir, "train_log_%s.json" % os.path.splitext(args.ckpt_name)[0]),
              "w", encoding="utf-8") as f:
        json.dump({"args": {k: str(v) for k, v in vars(args).items()},
                   "meta": meta, "history": history,
                   "region_val_iou": reg_iou}, f, ensure_ascii=False, indent=2)
    print("saved ->", ckpt)


if __name__ == "__main__":
    main()
