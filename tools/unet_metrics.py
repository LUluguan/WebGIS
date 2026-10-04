# -*- coding: utf-8 -*-
"""
unet_metrics.py — UNet 水体提取精度评估(演示样本集)

参考 Google Flood Hub 公开方法论 / NOAA FIM 精度评估文档的做法:
把模型精度指标写成常驻 JSON, 前端(unet.html / dashboard.html)展示"模型精度卡"。

口径: unet_out/samples/ 下 *_pred.png(模型预测) vs *_gt.png(标注真值)逐样本计算,
再宏平均。指标: IoU(Jaccard) / F1(Dice) / Precision / Recall / 混淆四格。
输出: unet_out/eval_metrics.json

运行: python tools/unet_metrics.py
"""
import glob, io, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "unet_out", "samples")
OUT_JSON = os.path.join(ROOT, "unet_out", "eval_metrics.json")


def decode(im, kind):
    """从演示样本渲染图解出水体掩膜: pred=蓝色覆盖[0,140,255], gt=绿色覆盖[0,255,140]。"""
    a = np.asarray(im.convert("RGB")).astype("int16")
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    if kind == "pred":
        return (r < 80) & (g > 90) & (g < 200) & (b > 200)
    return (r < 80) & (g > 200) & (b > 90) & (b < 200)


def main():
    preds = sorted(glob.glob(os.path.join(SAMPLES, "*_pred.png")))
    per, tp_all = [], dict(tp=0, fp=0, fn=0, tn=0)
    for pp in preds:
        gt_p = pp.replace("_pred.png", "_gt.png")
        if not os.path.exists(gt_p):
            continue
        pred = decode(Image.open(pp), "pred")
        gt = decode(Image.open(gt_p), "gt")
        if pred.shape != gt.shape:
            continue
        tp = int((pred & gt).sum())
        fp = int((pred & ~gt).sum())
        fn = int((~pred & gt).sum())
        tn = int((~pred & ~gt).sum())
        iou = tp / (tp + fp + fn) if (tp + fp + fn) else None
        f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else None
        prec = tp / (tp + fp) if (tp + fp) else None
        rec = tp / (tp + fn) if (tp + fn) else None
        name = os.path.basename(pp).replace("_pred.png", "")
        per.append({"sample": name, "iou": round(iou, 4) if iou is not None else None,
                    "f1": round(f1, 4) if f1 is not None else None,
                    "precision": round(prec, 4) if prec is not None else None,
                    "recall": round(rec, 4) if rec is not None else None})
        for k, v in zip(("tp", "fp", "fn", "tn"), (tp, fp, fn, tn)):
            tp_all[k] += v
    n = len(per)
    def mean(key):
        vals = [p[key] for p in per if p[key] is not None]
        return round(float(np.mean(vals)), 4) if vals else None
    out = {
        "dataset": "GF-FloodNet 验证场景演示样本(val_scenes, 未参与训练; 水面占比3–95%且预测非完全失败)",
        "n_samples": n,
        "macro": {"iou": mean("iou"), "f1": mean("f1"),
                  "precision": mean("precision"), "recall": mean("recall")},
        "confusion": tp_all,
        "per_sample": per,
        "note": "指标在演示样本集上计算(非完整测试集); 训练细节见 train_unet.py, 评估脚本 tools/unet_metrics.py",
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("samples:", n, "macro IoU:", out["macro"]["iou"], "F1:", out["macro"]["f1"])
    print("saved ->", OUT_JSON)


if __name__ == "__main__":
    main()
