# -*- coding: utf-8 -*-
"""
water_level_inversion.py — Route A 的「水位反演」核心(Route A 收尾拼图)

输入: 淹没/水体掩膜(来自 UNet 或任意来源)+ DEM
方法: 淹没边界线本身即「水位等高线」——取边界像元的 DEM 中位数 = 水面高程 W,
      水深 D = max(0, W - DEM)(仅在掩膜内)。
输出: 水深栅格 flood_out/inverted_depth.tif + 反演水位 W

说明: 这是与 bathtub_flood.py(重现期情景用「体积注水」推 W)并行的另一条腿——
      UNet 出「真实淹没范围」时, 用几何法(边界高程)反演水位, 无需水文模型。
"""
import os
import numpy as np
import rasterio
import tifffile

os.environ["PROJ_LIB"] = r"D:\Lib\site-packages\rasterio\proj_data"
os.environ["PROJ_DATA"] = r"D:\Lib\site-packages\rasterio\proj_data"


def invert(mask, dem):
    """mask: bool (True=水体/淹没); dem: float 高程。返回 (W, depth)。"""
    m = mask.astype(np.uint8)
    pad = np.pad(m, 1, mode="constant", constant_values=0)
    interior = pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:]
    boundary = m.astype(bool) & (~interior.astype(bool))
    if not boundary.any():
        raise ValueError("掩膜无有效边界(可能全图或全空)")
    W = float(np.median(dem[boundary]))
    depth = np.clip(W - dem, 0, None) * m
    return W, depth.astype("float32")


def main(mask_path, dem_path=r"D:\Competiton\dem\study_dtm.tif",
         out=r"D:\Competiton\flood_out\inverted_depth.tif"):
    with rasterio.open(dem_path) as src:
        dem = src.read(1).astype("float32")
        transform = src.transform
    mask = (tifffile.imread(mask_path).astype(np.uint8) > 0)

    # 若掩膜尺寸与 DEM 不同, 用最近邻重采样到 DEM 尺寸
    if mask.shape != dem.shape:
        from PIL import Image
        mask = np.array(Image.fromarray((mask * 255).astype("uint8"), "L")
                        .resize((dem.shape[1], dem.shape[0]), Image.NEAREST)) > 0

    W, depth = invert(mask, dem)
    with rasterio.open(out, "w", driver="GTiff", height=depth.shape[0],
                       width=depth.shape[1], count=1, dtype="float32",
                       crs="EPSG:4326", transform=transform, nodata=-9999.0) as dst:
        dst.write(depth, 1)
    print("反演水位 W = %.2f m  淹没像元 = %d  最大水深 = %.2f m" %
          (W, int(mask.sum()), float(depth[mask].max())))
    print("wrote ->", out)


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else None)
