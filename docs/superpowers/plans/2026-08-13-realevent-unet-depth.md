# 真实洪涝事件模块（UNet 驱动三维水深）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增"真实洪涝事件"模块——下载北江 2022-06 英德洪水的真实卫星影像（Sentinel-1 RTC SAR + Sentinel-2 光学），构建 5 波段输入，经 UNet 提取水体掩膜、边界水位反演得水深，并在新页面 `realevent.html` 中做三维可视化，让 UNet 实际产出本工程的水深。

**Architecture:** `sat_data.py`（STAC 检索/签名/窗口读取重投影）→ `unet_apply.py`（5 波段堆栈→UNet 掩膜→水位反演）→ `sar_change.py`（SAR 双时相变化验证）→ `realevent_beijiang.py`（主管线编排+导出 `realevent_out/`）→ `realevent.html`（三维页，复用 flood.html 模式）→ 服务层集成。

**Tech Stack:** Python 3.13（`D:\python.exe`）、numpy、rasterio 1.5.1、PIL、tifffile、torch（CPU）、FastAPI；前端 Cesium 1.95 + 天地图 WMTS；数据源 Microsoft Planetary Computer（匿名签名）。

## Global Constraints

- 所有 Python 脚本运行前需 `export PYTHONPATH=/d/Lib/site-packages`（`D:\python.exe` 的 site-packages 在 `D:\Lib\site-packages`）。
- 每个用到 rasterio 的脚本，`import rasterio` **之前**必须设 `os.environ["PROJ_LIB"]=r"D:\Lib\site-packages\rasterio\proj_data"` 与 `os.environ["PROJ_DATA"]=r"D:\Lib\site-packages\rasterio\proj_data"`（避免与 PostGIS 旧版 proj.db 冲突）。
- 测试用**普通 assert 脚本**（`D:\python.exe` 直接跑），不引入 pytest；命令如 `D:\python.exe tests/test_x.py`。
- UNet 模型：`unet_out/unet_water.pt`；输入 (5,128,128)，5 波段顺序 = `[S2_B02, S2_B03, S2_B04, S2_B08, S1_RTC_VV]`；模型类 `unet_model.UNet(5,1,base=32)`。
- 研究窗口（EPSG:4326）：`BBOX=[113.357, 24.127, 113.483, 24.253]`（英德城区+北江段，约 14km 方窗）。
- 目标网格：**EPSG:32649（UTM 49N）、10m**；S2 与 S1 RTC 原生即 32649 10m，DEM（GLO-30，4326）重投影到该网格。
- 数据下载：Planetary Computer STAC + `/api/sas/v1/sign?href=` 匿名签名（无需账号）；签名 URL 支持 rasterio range 窗口读取。
- 所有产物写入 `D:\Competiton\realevent_out\`。
- 日期：洪水影像 S1 RTC VV `2022-06-26`；灾前基线 `2022-06-02`；光学 S2 `2022-06-23`（云>30% 回退 `2022-07-13`）。

---

### Task 1: `sat_data.py` — STAC 检索 + 签名 + 窗口读取重投影

**Files:**
- Create: `D:\Competiton\sat_data.py`
- Test: `D:\Competiton\tests\test_sat_data.py`

**Interfaces:**
- Consumes: 无（首个模块）
- Produces:
  - `stac_search(collection: str, bbox: list[float], datetime: str, limit: int = 5, sortby: str | None = None) -> list[dict]`
  - `sign_url(href: str) -> str`
  - `lonlat_bbox_to_grid(bbox: list[float], dst_epsg: int, res_m: float) -> tuple[int, int, Affine]`
  - `read_window(href: str, bbox: list[float], dst_epsg: int, out_width: int, out_height: int) -> np.ndarray`（float32，(out_height, out_width)，无数据=NaN）

- [ ] **Step 1: 写失败测试（连通性冒烟：S1 RTC 洪水中影像窗口读取）**

创建 `D:\Competiton\tests\test_sat_data.py`：

```python
# -*- coding: utf-8 -*-
import os, sys, numpy as np
os.environ["PROJ_LIB"] = r"D:\Lib\site-packages\rasterio\proj_data"
os.environ["PROJ_DATA"] = r"D:\Lib\site-packages\rasterio\proj_data"
sys.path.insert(0, r"D:\Competiton")
import sat_data

BBOX = [113.357, 24.127, 113.483, 24.253]

def test_stac_sign_read_rtc():
    items = sat_data.stac_search("sentinel-1-rtc", BBOX,
                                 "2022-06-26T00:00:00Z/2022-06-27T00:00:00Z", limit=4)
    assert len(items) > 0, "STAC 检索不到 S1 RTC 洪水中影像"
    f = items[0]
    href = sat_data.sign_url(f["assets"]["vv"]["href"])
    w, h, _ = sat_data.lonlat_bbox_to_grid(BBOX, 32649, 10.0)
    arr = sat_data.read_window(href, BBOX, 32649, w, h)
    assert arr.shape == (h, w), "窗口形状不符: %s" % (arr.shape,)
    fin = np.isfinite(arr)
    assert fin.sum() > 0.1 * fin.size, "有效像元占比过低"
    print("RTC vv 窗口: shape=%s 有效像元=%.1f%% min=%.2f max=%.2f" %
          (arr.shape, 100 * fin.mean(), np.nanmin(arr), np.nanmax(arr)))

if __name__ == "__main__":
    test_stac_sign_read_rtc()
    print("test_sat_data OK")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/Competiton && export PYTHONPATH=/d/Lib/site-packages && D:\python.exe tests\test_sat_data.py`
Expected: `ModuleNotFoundError: No module named 'sat_data'`（模块尚未创建）

- [ ] **Step 3: 实现 `sat_data.py`**

创建 `D:\Competiton\sat_data.py`：

```python
# -*- coding: utf-8 -*-
"""sat_data.py — 卫星数据获取: Planetary Computer STAC 检索 + 匿名签名 + 窗口读取重投影。"""
import os
import numpy as np
import rasterio
from rasterio.warp import transform_bounds, reproject, Resampling
from rasterio.windows import from_bounds
from rasterio.transform import from_bounds as tf_from_bounds
from rasterio.transform import Affine

os.environ["PROJ_LIB"] = r"D:\Lib\site-packages\rasterio\proj_data"
os.environ["PROJ_DATA"] = r"D:\Lib\site-packages\rasterio\proj_data"

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"


def stac_search(collection, bbox, datetime, limit=5, sortby=None):
    import requests
    payload = {"collections": [collection], "bbox": list(bbox),
               "datetime": datetime, "limit": limit}
    if sortby:
        payload["sortby"] = [{"field": sortby, "direction": "asc"}]
    r = requests.post(STAC, json=payload, timeout=60)
    r.raise_for_status()
    return r.json().get("features", [])


def sign_url(href):
    import requests
    r = requests.get(SIGN, params={"href": href}, timeout=60)
    r.raise_for_status()
    return r.json()["href"]


def lonlat_bbox_to_grid(bbox, dst_epsg, res_m):
    """lonlat bbox -> 覆盖该框的 dst_epsg 网格 (width, height, dst_transform)。"""
    dst_crs = rasterio.crs.CRS.from_epsg(dst_epsg)
    left, bottom, right, top = transform_bounds(
        rasterio.crs.CRS.from_epsg(4326), dst_crs, *bbox)
    width = max(1, int(round((right - left) / res_m)))
    height = max(1, int(round((top - bottom) / res_m)))
    dst_transform = tf_from_bounds(left, bottom, right, top, width, height)
    return width, height, dst_transform


def read_window(href, bbox, dst_epsg, out_width, out_height):
    """读取 href 第1波段, 裁剪到 lon/lat bbox, 重投影到 dst_epsg 网格 (out_height, out_width)。"""
    dst_crs = rasterio.crs.CRS.from_epsg(dst_epsg)
    db = transform_bounds(rasterio.crs.CRS.from_epsg(4326), dst_crs, *bbox)
    dst_transform = tf_from_bounds(*db, out_width, out_height)
    with rasterio.open(href) as src:
        sb = transform_bounds(dst_crs, src.crs, *db)
        win = from_bounds(*sb, src.transform).round_offsets().round_lengths()
        if win.width < 1 or win.height < 1:
            raise ValueError("bbox 与栅格无交集: %s" % href)
        d = src.read(1, window=win, boundless=True).astype("float32")
        if src.nodata is not None:
            d = np.where(d == src.nodata, -9999.0, d)
        src_tf = src.transform * Affine.translation(win.col_off, win.row_off)
        out = np.full((out_height, out_width), np.nan, dtype="float32")
        reproject(d, out, src_transform=src_tf, src_crs=src.crs,
                  src_nodata=-9999.0, dst_transform=dst_transform, dst_crs=dst_crs,
                  resampling=Resampling.bilinear, dst_nodata=np.nan)
        return out
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /d/Competiton && export PYTHONPATH=/d/Lib/site-packages && D:\python.exe tests\test_sat_data.py`
Expected: `RTC vv 窗口: shape=(1418, 1305) ... test_sat_data OK`
（shape 具体值随窗口而定，关键是 shape 匹配且有效像元 >10%）

- [ ] **Step 5: 提交**

```bash
cd /d/Competiton
git add sat_data.py tests/test_sat_data.py
git commit -m "feat: 卫星数据获取模块(STAC检索/匿名签名/窗口重投影)"
```

---

### Task 2: `unet_apply.py` — 5 波段堆栈 → UNet 掩膜 → 水位反演

**Files:**
- Create: `D:\Competiton\unet_apply.py`
- Test: `D:\Competiton\tests\test_unet_apply.py`

**Interfaces:**
- Consumes: `unet_model.UNet`、`water_level_inversion.invert`、`unet_out/unet_water.pt`
- Produces:
  - `normalize(stack: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]`（z-score，(H,W,5)）
  - `resize_square(stack: np.ndarray, size: int = 128) -> np.ndarray`（(H,W,5)→(size,size,5)）
  - `predict_mask(stack: np.ndarray, size: int = 128) -> np.ndarray`（(size,size) uint8，0/255）
  - `invert_depth(mask: np.ndarray, dem: np.ndarray) -> tuple[float, np.ndarray]`（(W_level, depth)）

- [ ] **Step 1: 写失败测试（纯函数 + 真实模型冒烟）**

创建 `D:\Competiton\tests\test_unet_apply.py`：

```python
# -*- coding: utf-8 -*-
import os, sys, glob, numpy as np
os.environ.setdefault("PYTHONPATH", r"D:\Competiton")
sys.path.insert(0, r"D:\Competiton")
import unet_apply

def test_normalize():
    stack = np.stack([np.full((4, 4), v, dtype="float32") for v in (0, 10, 20, 30, 40)], axis=2)
    n, mean, std = unet_apply.normalize(stack)
    assert n.shape == (4, 4, 5)
    assert np.allclose(n.mean(axis=(0, 1)), 0, atol=1e-4), "归一化后均值应为0"
    assert np.allclose(n.std(axis=(0, 1)), 1, atol=1e-4), "归一化后标准差应为1"

def test_resize_square():
    s = np.random.rand(64, 64, 5).astype("float32")
    r = unet_apply.resize_square(s, 128)
    assert r.shape == (128, 128, 5)
    assert np.allclose(r[::2, ::2], s, atol=0.02), "放大后低频应近似保留"

def test_predict_on_flood_sample():
    import tifffile
    files = sorted(glob.glob(r"D:\Competiton\GF-FloodNet\GF-FloodNet-v1\images\China_*.tif"))
    assert files, "无 GF-FloodNet 中国样本"
    I = tifffile.imread(files[0]).astype("float32")          # (256,256,5) uint16
    mask = unet_apply.predict_mask(I, 128)
    frac = (mask > 0).mean()
    assert 0.01 < frac < 0.99, "洪水样本应检出一定比例水体, 实际 %.3f" % frac
    print("样本 %s 水体占比=%.1f%%" % (os.path.basename(files[0]), 100 * frac))

if __name__ == "__main__":
    test_normalize(); test_resize_square(); test_predict_on_flood_sample()
    print("test_unet_apply OK")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/Competiton && export PYTHONPATH=/d/Lib/site-packages && D:\python.exe tests\test_unet_apply.py`
Expected: `ModuleNotFoundError: No module named 'unet_apply'`

- [ ] **Step 3: 实现 `unet_apply.py`**

创建 `D:\Competiton\unet_apply.py`：

```python
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


def predict_mask(stack, size=128):
    """stack (H,W,5) -> 水体掩膜 (size,size) uint8 {0,255}; prob>0.5。"""
    model = _load_model()
    X = resize_square(stack, size)
    X, _, _ = normalize(X)
    Xt = torch.from_numpy(X.transpose(2, 0, 1)[None])          # (1,5,size,size)
    with torch.no_grad():
        prob = torch.sigmoid(model(Xt))[0, 0].numpy()
    return (prob > 0.5).astype(np.uint8) * 255


def invert_depth(mask, dem):
    """mask (H,W) bool/uint8, dem (H,W) float -> (W_level, depth)。复用 water_level_inversion.invert。"""
    from water_level_inversion import invert
    m = mask.astype(bool)
    if m.shape != dem.shape:
        m = np.array(Image.fromarray((m * 255).astype("uint8"), "L")
                     .resize((dem.shape[1], dem.shape[0]), Image.NEAREST)) > 0
    return invert(m, dem)


if __name__ == "__main__":
    import tifffile, glob, sys
    f = sorted(glob.glob(r"D:\Competiton\GF-FloodNet\GF-FloodNet-v1\images\China_*.tif"))[0]
    m = predict_mask(tifffile.imread(f).astype("float32"), 128)
    print("%s 水体占比=%.1f%%" % (os.path.basename(f), 100 * (m > 0).mean()))
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /d/Competiton && export PYTHONPATH=/d/Lib/site-packages && D:\python.exe tests\test_unet_apply.py`
Expected: 三个测试全过，末尾 `test_unet_apply OK`；`test_predict_on_flood_sample` 打印水体占比在 1%~99% 之间。

- [ ] **Step 5: 提交**

```bash
cd /d/Competiton
git add unet_apply.py tests/test_unet_apply.py
git commit -m "feat: UNet 应用模块(5波段堆栈/归一化/水体掩膜/水位反演)"
```

---

### Task 3: `sar_change.py` — SAR 双时相变化检测（验证）

**Files:**
- Create: `D:\Competiton\sar_change.py`
- Test: `D:\Competiton\tests\test_sar_change.py`

**Interfaces:**
- Consumes: 无
- Produces: `change_mask(vv_flood: np.ndarray, vv_base: np.ndarray, drop_db: float = -8.0) -> np.ndarray`（bool，(H,W)，True=洪水新增水面）

- [ ] **Step 1: 写失败测试**

创建 `D:\Competiton\tests\test_sar_change.py`：

```python
# -*- coding: utf-8 -*-
import os, sys, numpy as np
sys.path.insert(0, r"D:\Competiton")
import sar_change

def test_change_mask():
    base = np.full((32, 32), 12.0, dtype="float32")          # 陆地 dB
    flood = base.copy()
    flood[8:24, 8:24] = 0.0                                   # 中央区被水淹没 -> -12dB
    m = sar_change.change_mask(flood, base, drop_db=-8.0)
    assert m[8:24, 8:24].all(), "淹没区应全判为新增水面"
    assert not m[:4, :4].any(), "未变化区不应误判"

def test_to_db_linear():
    lin = np.array([[100.0, 100.0], [100.0, 1.0]], dtype="float32")
    db = sar_change.to_db(lin)
    assert abs(db[0, 0] - 20.0) < 1e-3, "100 线性幅度应≈20dB"

if __name__ == "__main__":
    test_change_mask(); test_to_db_linear()
    print("test_sar_change OK")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/Competiton && export PYTHONPATH=/d/Lib/site-packages && D:\python.exe tests\test_sar_change.py`
Expected: `ModuleNotFoundError: No module named 'sar_change'`

- [ ] **Step 3: 实现 `sar_change.py`**

创建 `D:\Competiton\sar_change.py`：

```python
# -*- coding: utf-8 -*-
"""sar_change.py — Sentinel-1 RTC VV 双时相变化检测: 后向散射骤降 = 洪水新增水面。"""
import numpy as np


def to_db(arr, linear_thresh=1.0):
    """若数组取值呈线性幅度(中位数>1), 转 dB; 否则原样(dB)。"""
    v = arr[np.isfinite(arr)]
    if v.size == 0:
        return arr
    if np.nanmedian(v) > linear_thresh:
        with np.errstate(divide="ignore"):
            return 10.0 * np.log10(np.clip(arr, 1e-8, None))
    return arr


def change_mask(vv_flood, vv_base, drop_db=-8.0):
    """vv_flood 与 vv_base 同为 RTC VV(线性或dB)。返回 True=新增水面(后向散射骤降)。"""
    f = to_db(vv_flood.astype("float32"))
    b = to_db(vv_base.astype("float32"))
    valid = np.isfinite(f) & np.isfinite(b)
    diff = np.full(f.shape, np.nan, dtype="float32")
    diff[valid] = f[valid] - b[valid]
    return (diff < drop_db) & valid
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /d/Competiton && export PYTHONPATH=/d/Lib/site-packages && D:\python.exe tests\test_sar_change.py`
Expected: `test_sar_change OK`

- [ ] **Step 5: 提交**

```bash
cd /d/Competiton
git add sar_change.py tests/test_sar_change.py
git commit -m "feat: SAR 双时相变化检测(洪水新增水面验证)"
```

---

### Task 4: `realevent_beijiang.py` — 主管线（下载→UNet→反演→导出）

**Files:**
- Create: `D:\Competiton\realevent_beijiang.py`

**Interfaces:**
- Consumes: `sat_data.*`（Task 1）、`unet_apply.*`（Task 2）、`sar_change.change_mask`（Task 3）
- Produces（写入 `D:\Competiton\realevent_out\`）:
  - `truecolor.png`、`flood_mask.png`、`depth.png`、`sar_change.png`、`depth.tif`
  - `realevent.json`：键 `event, flood_image, optical_image, method, bbox, epsg, grid_px, water_level_m, flooded_area_km2, mean_depth_m, max_depth_m, unet_sar_iou, cloud_pct, reliability, flood_bbox, assets`

- [ ] **Step 1: 写主管线脚本（含步骤打印）**

创建 `D:\Competiton\realevent_beijiang.py`：

```python
# -*- coding: utf-8 -*-
"""
realevent_beijiang.py — 北江 2022-06 英德洪水真实事件: 下载卫星影像 → 5波段 → UNet → 水位反演 → 导出。
输出到 realevent_out/。用法: python realevent_beijiang.py
"""
import os, json
import numpy as np
import rasterio
from PIL import Image

os.environ["PROJ_LIB"] = r"D:\Lib\site-packages\rasterio\proj_data"
os.environ["PROJ_DATA"] = r"D:\Lib\site-packages\rasterio\proj_data"

import sat_data
import unet_apply
import sar_change

OUT = r"D:\Competiton\realevent_out"
EPSG = 32649
RES = 10.0
BBOX = [113.357, 24.127, 113.483, 24.253]   # 英德城区 + 北江段
FLOW_DT = "2022-06-26T00:00:00Z/2022-06-27T00:00:00Z"
BASE_DT = "2022-06-02T00:00:00Z/2022-06-03T00:00:00Z"
S2_DT = "2022-06-22T00:00:00Z/2022-06-24T00:00:00Z"
S2_FALLBACK_DT = "2022-07-12T00:00:00Z/2022-07-14T00:00:00Z"
DEM_URL = ("https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/"
           "Copernicus_DSM_COG_10_N24_00_E113_00_DEM/Copernicus_DSM_COG_10_N24_00_E113_00_DEM.tif")
S2_BANDS = ["B02", "B03", "B04", "B08", "SCL"]
SIZE = 128
DEPTH_CAP = 6.0


def grid_dims():
    w, h, _ = sat_data.lonlat_bbox_to_grid(BBOX, EPSG, RES)
    return w, h


def get_asset(collection, dt, asset):
    """检索覆盖 BBOX 的 item 并返回签名后的 asset URL。"""
    items = sat_data.stac_search(collection, BBOX, dt, limit=6)
    best = None
    for f in items:
        bb = f.get("bbox")
        if bb and len(bb) == 4 and bb[0] <= BBOX[2] and bb[2] >= BBOX[0] \
                and bb[1] <= BBOX[3] and bb[3] >= BBOX[1]:
            best = f
            break
    if best is None:
        raise RuntimeError("collection=%s dt=%s 无覆盖 BBOX 的 item" % (collection, dt))
    return sat_data.sign_url(best["assets"][asset]["href"])


def read_band(collection, dt, asset):
    w, h = grid_dims()
    href = get_asset(collection, dt, asset)
    return sat_data.read_window(href, BBOX, EPSG, w, h)


def cloud_frac(scl):
    valid = np.isfinite(scl)
    if not valid.any():
        return 1.0
    return float(((scl >= 3) & (scl <= 7) & valid).sum() / valid.sum())


def depth_rgba(depth):
    d = np.asarray(depth, dtype="float32")
    m = np.isfinite(d) & (d > 0.05)
    t = np.clip(d / DEPTH_CAP, 0, 1)
    img = np.zeros(d.shape + (4,), dtype=np.uint8)
    img[..., 0] = (166 * (1 - t)).astype("uint8")
    img[..., 1] = (227 - 176 * t).astype("uint8")
    img[..., 2] = (255 - 153 * t).astype("uint8")
    img[..., 3] = np.where(m, 200, 0).astype("uint8")
    return img


def truecolor_rgb(b04, b03, b02, pct=98):
    arr = np.stack([b04, b03, b02], axis=2).astype("float32")
    vmax = np.nanpercentile(arr, pct)
    rgb = np.clip(arr / max(vmax, 1e-6), 0, 1)
    rgb[~np.isfinite(rgb)] = 0
    return (rgb * 255).astype(np.uint8)


def main():
    os.makedirs(OUT, exist_ok=True)
    w, h = grid_dims()
    print("网格 %dx%d @%.0fm EPSG:%d" % (w, h, RES, EPSG))

    # ---- S1 RTC VV: 洪水中 + 灾前基线 ----
    print("下载 S1 RTC VV 洪水中(2022-06-26)...")
    vv_flood = read_band("sentinel-1-rtc", FLOW_DT, "vv")
    print("下载 S1 RTC VV 灾前(2022-06-02)...")
    vv_base = read_band("sentinel-1-rtc", BASE_DT, "vv")

    # ---- S2 光学(带云量回退) ----
    print("下载 S2 光学(2022-06-23)...")
    s2 = {b: read_band("sentinel-2-l2a", S2_DT, b) for b in S2_BANDS}
    cf = cloud_frac(s2["SCL"])
    print("窗口云量 %.1f%%" % (100 * cf))
    if cf > 0.30:
        print("  云量过高 -> 回退 %s" % S2_FALLBACK_DT[:10])
        s2 = {b: read_band("sentinel-2-l2a", S2_FALLBACK_DT, b) for b in S2_BANDS}
        cf = cloud_frac(s2["SCL"])
        print("  回退后云量 %.1f%%" % (100 * cf))

    # ---- DEM ----
    print("下载 GLO-30 DEM...")
    dem = sat_data.read_window(DEM_URL, BBOX, EPSG, w, h)

    # ---- 5波段堆栈 + UNet ----
    print("构建5波段堆栈 + UNet 推理...")
    stack = np.stack([s2["B02"], s2["B03"], s2["B04"], s2["B08"], vv_flood], axis=2)
    mask = unet_apply.predict_mask(stack, SIZE)                       # (128,128)
    mask_full = np.array(Image.fromarray(mask, "L").resize((w, h), Image.NEAREST)) > 0

    # ---- 水位反演 ----
    W_level, depth = unet_apply.invert_depth(mask_full, dem)
    dep = depth.copy()
    dep[~mask_full] = np.nan
    flooded_km2 = float(mask_full.sum() * RES * RES / 1e6)
    mean_dep = float(np.nanmean(dep))
    max_dep = float(np.nanmax(dep))
    print("反演水位 W=%.2f m  淹没面积=%.2f km²  平均水深=%.2f m  最大水深=%.2f m"
          % (W_level, flooded_km2, mean_dep, max_dep))

    # ---- SAR 变化验证 ----
    change = sar_change.change_mask(vv_flood, vv_base)
    inter = (change & mask_full).sum()
    union = (change | mask_full).sum()
    iou = inter / max(1, union)
    print("UNet 掩膜 vs SAR变化 IoU=%.3f" % iou)

    # ---- 淹没范围包络(供前端水面) ----
    flood_bbox = None
    if mask_full.any():
        ys, xs = np.where(mask_full)
        _, _, dst_tf = sat_data.lonlat_bbox_to_grid(BBOX, EPSG, RES)
        x0, y0 = rasterio.transform.xy(dst_tf, ys.min(), xs.min(), offset="center")
        x1, y1 = rasterio.transform.xy(dst_tf, ys.max(), xs.max(), offset="center")
        lons, lats = rasterio.warp.transform(rasterio.crs.CRS.from_epsg(EPSG),
                                             rasterio.crs.CRS.from_epsg(4326),
                                             [x0, x1], [y0, y1])
        flood_bbox = [float(min(lons)), float(min(lats)),
                      float(max(lons)), float(max(lats))]

    # ---- 导出 ----
    Image.fromarray(truecolor_rgb(s2["B04"], s2["B03"], s2["B02"])).save(
        os.path.join(OUT, "truecolor.png"))
    Image.fromarray((mask_full * 255).astype("uint8"), "L").save(
        os.path.join(OUT, "flood_mask.png"))
    Image.fromarray(depth_rgba(depth), "RGBA").save(os.path.join(OUT, "depth.png"))
    Image.fromarray((change * 255).astype("uint8"), "L").save(
        os.path.join(OUT, "sar_change.png"))
    with rasterio.open(os.path.join(OUT, "depth.tif"), "w", driver="GTiff",
                       height=depth.shape[0], width=depth.shape[1], count=1,
                       dtype="float32", crs="EPSG:%d" % EPSG,
                       transform=sat_data.lonlat_bbox_to_grid(BBOX, EPSG, RES)[2],
                       nodata=-9999.0) as dst:
        dst.write(np.where(np.isfinite(depth), depth, -9999.0).astype("float32"), 1)
    np.savez(os.path.join(OUT, "_cache.npz"), vv_flood=vv_flood, vv_base=vv_base,
             dem=dem, mask_full=mask_full, depth=depth)

    reliability = "高" if iou > 0.7 else ("中" if iou > 0.4 else "低")
    ev = {
        "event": "北江特大洪水(2022-06) · 英德城区",
        "flood_image": "Sentinel-1 RTC VV 2022-06-26 10:34Z",
        "optical_image": "Sentinel-2 L2A %s" % S2_DT[:10],
        "method": "UNet(5波段: S2 B2/B3/B4/B8 + S1 RTC VV) 水体提取 → 边界水位反演",
        "bbox": BBOX, "epsg": EPSG, "grid_px": RES, "size": SIZE,
        "water_level_m": round(W_level, 2), "flooded_area_km2": round(flooded_km2, 3),
        "mean_depth_m": round(mean_dep, 2), "max_depth_m": round(max_dep, 2),
        "unet_sar_iou": round(iou, 3), "cloud_pct": round(100 * cf, 1),
        "reliability": reliability, "flood_bbox": flood_bbox,
        "assets": {"truecolor": "truecolor.png", "mask": "flood_mask.png",
                   "depth": "depth.png", "sar_change": "sar_change.png"},
    }
    with open(os.path.join(OUT, "realevent.json"), "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    print("导出完成 ->", OUT, "| 可靠性:", reliability)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行主管线**

Run: `cd /d/Competiton && export PYTHONPATH=/d/Lib/site-packages && D:\python.exe realevent_beijiang.py`
Expected: 依次打印下载/云量/反演/IoU/导出信息；`realevent_out/` 下生成 6 个文件。
**若反演报 `ValueError: 掩膜无有效边界`**：说明 UNet 掩膜过稀/全图，检查 `flood_mask.png` 与打印的 IoU，按下面 Step 3 的调参步骤处理。

- [ ] **Step 3: 目视核验 + 必要时调参**

- 打开 `realevent_out/truecolor.png` 与 `flood_mask.png`、`depth.png`，确认掩膜贴合北江河谷/洪泛区、与已知英德 2022-06 被淹范围目视吻合。
- 若掩膜**过碎/噪声大**：在 `realevent_beijiang.py` 中把 `unet_apply.predict_mask(stack, SIZE)` 的阈值从 0.5 提高（在 `predict_mask` 里改为 `prob > 0.7`），重跑。
- 若掩膜**几乎无水体**：将 `predict_mask` 阈值降到 `prob > 0.3`，重跑。
- 记录最终阈值选择到 `realevent.json`（在 `method` 里注明 `thr=0.5`）。

- [ ] **Step 4: 确认输出与 JSON**

Run: `cd /d/Competiton && D:\python.exe -c "import json; d=json.load(open('realevent_out/realevent.json',encoding='utf-8')); print({k:d[k] for k in ('water_level_m','flooded_area_km2','unet_sar_iou','reliability','flood_bbox')})"`
Expected: 输出合理的 W（量级应与英德 2022-06 洪峰水位 ~35m 对应，具体值视窗口）、面积、IoU、可靠性、`flood_bbox` 非空。

- [ ] **Step 5: 提交**

```bash
cd /d/Competiton
git add realevent_beijiang.py
git add realevent_out/
git commit -m "feat: 北江2022-06英德真实事件管线(UNet掩膜→水位反演→导出)"
```

---

### Task 5: `realevent.html` — 三维可视化页（体现 UNet 水深作用）

**Files:**
- Create: `D:\Competiton\realevent.html`
- Create: `D:\Competiton\realevent_data.js`（把 `realevent_out/realevent.json` 内容写成 `window.REALEVENT = {...}`）

**Interfaces:**
- Consumes: `realevent_out/realevent.json`、`realevent_out/truecolor.png`、`flood_mask.png`、`depth.png`、`sar_change.png`、Cesium 1.95 CDN、天地图 WMTS
- Produces: 无（前端页面）

- [ ] **Step 1: 生成数据 JS**

创建 `D:\Competiton\realevent_data.js`：

```bash
cd /d/Competiton
D:\python.exe -c "import json;d=json.load(open('realevent_out/realevent.json',encoding='utf-8'));open('realevent_data.js','w',encoding='utf-8').write('window.REALEVENT = %s;'%json.dumps(d,ensure_ascii=False))"
```

- [ ] **Step 2: 写 `realevent.html`**

创建 `D:\Competiton\realevent.html`（复用 flood.html 的左面板/Cesium/天地图样式，`Cesium.Ion.defaultAccessToken` 与 `TDT_KEY` 从 flood.html 复制）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>真实洪涝事件 · UNet 反演水深 · 北江2022-06英德</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/cesium@1.95/Build/Cesium/Widgets/widgets.css" />
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body, #cesiumContainer { width: 100%; height: 100%; overflow: hidden; font-family: 'Microsoft YaHei', sans-serif; }
    #panel { position: absolute; top: 16px; left: 16px; width: 330px;
      background: rgba(13,25,43,0.93); color: #e6f1ff; border: 1px solid rgba(79,195,247,0.35);
      border-radius: 12px; padding: 16px; z-index: 999; box-shadow: 0 8px 32px rgba(0,0,0,0.45); }
    #panel h1 { font-size: 16px; color: #4fc3f7; margin-bottom: 4px; }
    #panel .sub { font-size: 12px; color: #9fb3cc; margin-bottom: 12px; }
    .pipe { display: flex; gap: 6px; margin-bottom: 12px; }
    .pipe .step { flex: 1; text-align: center; font-size: 10px; color: #cfe6ff; }
    .pipe img { width: 100%; height: 64px; object-fit: cover; border-radius: 6px; border: 1px solid rgba(79,195,247,0.3); }
    .row { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
    .btn { flex: 1 1 40%; padding: 7px 4px; border: 1px solid rgba(79,195,247,0.4);
      background: rgba(79,195,247,0.08); color: #cfe6ff; border-radius: 8px; cursor: pointer;
      font-size: 12px; text-align: center; }
    .btn.active { background: #0288d1; color: #fff; border-color: #4fc3f7; }
    .stats { background: rgba(255,255,255,0.04); border-radius: 8px; padding: 10px; margin-bottom: 10px; }
    .stat { display: flex; justify-content: space-between; font-size: 12px; padding: 3px 0;
      border-bottom: 1px dashed rgba(255,255,255,0.08); }
    .stat .k { color: #9fb3cc; } .stat .v { color: #4fc3f7; font-weight: 600; }
    .compare { background: rgba(255,255,255,0.04); border-radius: 8px; padding: 10px; font-size: 11px; line-height: 1.6; color: #9fb3cc; }
    .legend { display: flex; align-items: center; gap: 8px; font-size: 11px; color: #9fb3cc; margin-top: 10px; }
    .grad { flex: 1; height: 12px; border-radius: 6px; background: linear-gradient(90deg,#a6e3ff,#4fc3f7,#0277bd,#003366); }
    .note { font-size: 11px; color: #6b8299; margin-top: 10px; line-height: 1.5; }
  </style>
</head>
<body>
  <div id="cesiumContainer"></div>
  <div id="panel">
    <h1>真实洪涝事件 · UNet 反演水深</h1>
    <div class="sub" id="evName">北江特大洪水 · 英德城区 · 2022-06</div>

    <div class="pipe">
      <div class="step"><img id="pImg" src="realevent_out/truecolor.png" /><div>①卫星影像</div></div>
      <div class="step"><img id="mImg" src="realevent_out/flood_mask.png" /><div>②UNet掩膜</div></div>
      <div class="step"><img id="dImg" src="realevent_out/depth.png" /><div>③水深反演</div></div>
    </div>

    <div class="row" id="toggles">
      <div class="btn active" data-l="truecolor">卫星真彩</div>
      <div class="btn" data-l="mask">UNet 掩膜</div>
      <div class="btn" data-l="depth">反演水深</div>
      <div class="btn" data-l="sar">SAR 变化</div>
    </div>

    <div class="stats" id="stats"></div>

    <div class="compare" id="compare"></div>

    <div class="legend"><span>水深</span><div class="grad"></div><span>深</span></div>

    <div class="note" id="note">
      水深由 UNet 提取的淹没范围经边界水位反演得到：W = median(DEM[边界])，D = max(0, W−DEM)。
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/cesium@1.95/Build/Cesium/Cesium.js"></script>
  <script src="realevent_data.js"></script>
  <script>
    Cesium.Ion.defaultAccessToken = 'PASTE_FLOOD_HTML_TOKEN';
    var TDT_KEY = 'PASTE_TDT_KEY';
    var R = window.REALEVENT;

    var viewer = new Cesium.Viewer('cesiumContainer', {
      baseLayerPicker: false, animation: false, timeline: false, geocoder: false,
      homeButton: false, infoBox: false, sceneModePicker: false,
      navigationHelpButton: false, fullscreenButton: true
    });
    viewer.imageryLayers.addImageryProvider(new Cesium.WebMapTileServiceImageryProvider({
      url: 'https://{s}.tianditu.gov.cn/img_w/wmts?service=wmts&request=GetTile&version=1.0.0'
         + '&LAYER=img&tileMatrixSet=w&TileMatrix={TileMatrix}&TileRow={TileRow}&TileCol={TileCol}'
         + '&style=default&format=tiles&tk=' + TDT_KEY,
      layer: 'img', style: 'default', format: 'tiles', tileMatrixSetID: 'w',
      subdomains: ['t0','t1','t2','t3','t4','t5','t6','t7'], maximumLevel: 18
    }));

    var RECT = Cesium.Rectangle.fromDegrees(R.bbox[0], R.bbox[1], R.bbox[2], R.bbox[3]);
    var layers = {};

    function addLayer(name, url, alpha) {
      var l = viewer.imageryLayers.addImageryProvider(new Cesium.SingleTileImageryProvider({
        url: url, rectangle: RECT }));
      l.alpha = alpha; l.show = (name === 'truecolor');
      layers[name] = l;
    }
    addLayer('truecolor', 'realevent_out/truecolor.png', 1.0);
    addLayer('mask', 'realevent_out/flood_mask.png', 0.75);
    addLayer('depth', 'realevent_out/depth.png', 0.72);
    addLayer('sar', 'realevent_out/sar_change.png', 0.72);

    // 三维水面: 淹没包络矩形在水位 W 处(示意 UNet 反演的水位)
    var fb = R.flood_bbox || R.bbox;
    var water = viewer.entities.add({
      polygon: {
        hierarchy: Cesium.Cartesian3.fromDegreesArray([fb[0], fb[1], fb[2], fb[1], fb[2], fb[3], fb[0], fb[3]]),
        height: R.water_level_m,
        material: Cesium.Color.fromCssColorString('#2196f3').withAlpha(0.35),
        outline: false
      }
    });

    document.querySelectorAll('#toggles .btn').forEach(function(b) {
      b.onclick = function() {
        document.querySelectorAll('#toggles .btn').forEach(function(x) { x.classList.remove('active'); });
        b.classList.add('active');
        Object.keys(layers).forEach(function(k) { layers[k].show = (k === b.dataset.l); });
      };
    });

    document.getElementById('stats').innerHTML =
      '<div class="stat"><span class="k">反演水面高程 W</span><span class="v">' + R.water_level_m + ' m</span></div>' +
      '<div class="stat"><span class="k">淹没面积</span><span class="v">' + R.flooded_area_km2 + ' km²</span></div>' +
      '<div class="stat"><span class="k">平均水深</span><span class="v">' + R.mean_depth_m + ' m</span></div>' +
      '<div class="stat"><span class="k">最大水深</span><span class="v">' + R.max_depth_m + ' m</span></div>' +
      '<div class="stat"><span class="k">UNet vs SAR IoU</span><span class="v">' + R.unet_sar_iou + '</span></div>' +
      '<div class="stat"><span class="k">可靠性</span><span class="v">' + R.reliability + '</span></div>';
    document.getElementById('compare').innerHTML =
      '<b>UNet 在水深模拟中的作用</b><br/>' +
      '真实卫星影像 → <span style="color:#4fc3f7">UNet 提取淹没范围</span> → ' +
      '边界水位反演得真实水面高程 W → 水深 D = max(0, W−DEM)。<br/>' +
      '对比浴缸法(假设降雨体积注水)，UNet 直接由影像得到真实水位，无需水文假设。';

    viewer.camera.setView({
      destination: Cesium.Cartesian3.fromDegrees((R.bbox[0]+R.bbox[2])/2, (R.bbox[1]+R.bbox[3])/2, 20000),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-55), roll: 0 }
    });
  </script>
</body>
</html>
```

> 实现时：把 flood.html 第 96 行 `Cesium.Ion.defaultAccessToken` 与第 97 行 `TDT_KEY` 的实值复制替换上面的 `PASTE_...` 占位符。

- [ ] **Step 3: 本地服务验证页面**

Run: `cd /d/Competiton && export PYTHONPATH=/d/Lib/site-packages && (D:\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8001 > /tmp/uv.log 2>&1 &) && sleep 6 && curl -s -o /dev/null -w "realevent.html -> %{http_code}\n" http://127.0.0.1:8001/realevent.html && curl -s -o /dev/null -w "realevent_data.js -> %{http_code}\n" http://127.0.0.1:8001/realevent_data.js && curl -s -o /dev/null -w "truecolor.png -> %{http_code}\n" http://127.0.0.1:8001/realevent_out/truecolor.png`
Expected: 三个均 `200`。

- [ ] **Step 4: 浏览器打开人工核验**

Run: `cmd //c start "" "http://127.0.0.1:8001/realevent.html"`
人工确认：左侧四步管线图正常显示、图层切换生效、三维水面出现在 W 高度、统计面板数据正确。

- [ ] **Step 5: 提交**

```bash
cd /d/Competiton
git add realevent.html realevent_data.js
git commit -m "feat: 真实洪涝事件三维页(UNet掩膜/反演水深/图层切换)"
```

---

### Task 6: 服务层集成（`/api/realevent` + 主页入口）

**Files:**
- Modify: `D:\Competiton\app.py`
- Modify: `D:\Competiton\index.html`
- Test: `D:\Competiton\tests\test_api_realevent.py`

**Interfaces:**
- Consumes: `realevent_out/realevent.json`
- Produces: `GET /api/realevent` → `Response`（`application/json`，内容为 realevent.json）

- [ ] **Step 1: 写失败测试**

创建 `D:\Competiton\tests\test_api_realevent.py`：

```python
# -*- coding: utf-8 -*-
import os, sys, json, io
sys.path.insert(0, r"D:\Competiton")
os.chdir(r"D:\Competiton")
from fastapi.testclient import TestClient
import app

def test_realevent():
    c = TestClient(app.app)
    r = c.get("/api/realevent")
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["water_level_m"] > 0, "W 应为正"
    assert d["flooded_area_km2"] > 0, "淹没面积应为正"
    assert d["flood_bbox"], "应有淹没包络"
    print("api realevent OK:", d["water_level_m"], d["flooded_area_km2"], d["reliability"])

if __name__ == "__main__":
    test_realevent()
    print("test_api_realevent OK")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /d/Competiton && export PYTHONPATH=/d/Lib/site-packages && D:\python.exe tests\test_api_realevent.py`
Expected: `AssertionError`（接口未实现 → 404）或 `KeyError: 'water_level_m'`

- [ ] **Step 3: 在 app.py 加接口**

在 `app.py` 的 `# ---------------- API ----------------` 区块末尾（`/api/depth_hist` 之后）插入：

```python
@app.get("/api/realevent")
def realevent():
    """真实洪涝事件(北江2022-06英德) UNet 反演水深元数据。"""
    p = os.path.join(ROOT, "realevent_out", "realevent.json")
    if not os.path.exists(p):
        return JSONResponse({"error": "realevent 数据未生成，请先运行 realevent_beijiang.py"}, status_code=404)
    with open(p, encoding="utf-8") as f:
        return json.load(f)
```

- [ ] **Step 4: 在 index.html 加入口**

读取 `D:\Competiton\index.html` 入口按钮区，追加一个链接到 `realevent.html` 的按钮（样式沿用现有入口按钮），文案："真实事件 · UNet 反演水深"。若 index.html 未找到入口区，直接在 `<body>` 顶部加：

```html
<a href="realevent.html" style="position:absolute;top:12px;right:12px;z-index:999;padding:8px 12px;background:#0288d1;color:#fff;border-radius:8px;text-decoration:none;font-size:13px;">真实事件 · UNet 反演水深</a>
```

- [ ] **Step 5: 运行确认通过**

Run: `cd /d/Competiton && export PYTHONPATH=/d/Lib/site-packages && D:\python.exe tests\test_api_realevent.py`
Expected: `api realevent OK: ... test_api_realevent OK`
再跑：`curl -s http://127.0.0.1:8001/api/realevent`（若 uvicorn 未运行则先启动）应返回 JSON。

- [ ] **Step 6: 提交**

```bash
cd /d/Competiton
git add app.py index.html tests/test_api_realevent.py
git commit -m "feat: 服务层集成 realevent 接口与主页入口"
```

---

## 验证（最终）

- [ ] 运行主管线成功，`realevent_out/` 六件产物齐全，`realevent.json` 的 W≈35m 量级、IoU>0.7。
- [ ] `curl /realevent.html`、`/api/realevent` 均 200。
- [ ] 浏览器打开 `realevent.html`：四步管线图、图层切换、三维水面、统计面板正常，能体现"UNet 提取淹没范围 → 反演水深 → 三维"链路。

## Self-Review 备注

- 规格 §4 管线 ↔ Task 1/2/3/4；§5 页面 ↔ Task 5；§6 服务集成 ↔ Task 6；§7 成功标准 ↔ 最终验证；§8 风险(云量回退/域迁移 IoU) ↔ Task 4 Step 3 与 reliability 字段。
- 类型一致性：`read_window` 返回 float32 (H,W)（Task 1）→ `unet_apply.normalize/resize_square` 接受 (H,W,5)（Task 2）→ `predict_mask` 返回 (size,size) uint8 → `invert_depth(mask, dem)` 返回 `(float, ndarray)` → Task 4 使用一致；`change_mask` 返回 bool (H,W)（Task 3）→ Task 4 与 `mask_full` 求 IoU。
