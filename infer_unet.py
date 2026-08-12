# -*- coding: utf-8 -*-
"""
infer_unet.py — UNet 水体提取推理: 输入多光谱影像(5波段 .tif) -> 输出水体二值掩膜 PNG。
用法:
  python infer_unet.py --img <5波段.tif> --ckpt unet_out/unet_water.pt --out out_mask.png
"""
import argparse, os
import numpy as np
import tifffile
import torch
from PIL import Image
from unet_model import UNet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", required=True)
    ap.add_argument("--ckpt", default=r"D:\Competiton\unet_out\unet_water.pt")
    ap.add_argument("--out", default=r"D:\Competiton\unet_out\infer_mask.png")
    ap.add_argument("--size", type=int, default=256)
    args = ap.parse_args()

    ck = torch.load(args.ckpt, map_location="cpu")
    mean, std = ck["mean"], ck["std"]

    I = tifffile.imread(args.img).astype(np.float32)   # (H, W, 5)
    h, w = I.shape[:2]
    if h != args.size or w != args.size:
        from PIL import Image
        I = np.stack([np.array(Image.fromarray(I[..., b].astype("uint16"), "I;16").resize((args.size, args.size)))
                      for b in range(5)], axis=2).astype(np.float32)
    X = I.transpose(2, 0, 1)                          # (5, H, W)
    for b in range(5):
        X[b] = (X[b] - mean[b]) / std[b]
    Xt = torch.from_numpy(X[None])

    model = UNet(5, 1, base=ck["base"]); model.load_state_dict(ck["state_dict"]); model.eval()
    with torch.no_grad():
        prob = torch.sigmoid(model(Xt))[0, 0].numpy()  # (H, W)
    mask = (prob > 0.5).astype(np.uint8) * 255

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    Image.fromarray(mask, "L").save(args.out)
    print("water fraction = %.3f%%  -> %s" % (100.0 * mask.mean() / 255.0, args.out))


if __name__ == "__main__":
    main()
