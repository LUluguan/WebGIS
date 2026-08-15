# 在线模拟（自定义雨量 → 浴缸法实时反演）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增「在线模拟」模式：用户输入任意 24h 雨量(mm)，后端用与重现期档位同口径的浴缸法实时反演水位/淹没范围/分区占比，前端在三维场景渲染并更新分区标签。

**Architecture:** 新增后端 `GET /api/online_sim?rain_mm=R`（DTM 模块级缓存 + 内联浴缸法二分 + 与 `/api/zone_flood` 同算法的分区占比），前端在 index.html 新增第三个模式按钮「在线模拟」及其输入/统计区块，`runOnline()` 调接口重建水体并复用 `updateZones()`。

**Tech Stack:** FastAPI (app.py), rasterio, numpy, CesiumJS 1.95 (index.html), 无新依赖。

**Spec:** `docs/design/2026-08-15-online-sim-design.md`

## Global Constraints

- 雨量输入范围：前端 10–2000mm；后端 `rain_mm: float = Query(..., gt=0, le=2000)`（越界 422）
- 与重现期场景同口径：`RUNOFF_COEF = 0.50`、`DEPTH_THRESH = 0.05`、陆域 `z>0`（排除珠江河道）、DTM = `dem/study_dtm.tif`
- 分区占比算法必须与 `/api/zone_flood` 逐字一致：`flood = (depth>0) & land`，`grid=3`
- 前端三维水体：`material = Cesium.Color.fromCssColorString('#2196f3').withAlpha(0.5)`，`extrudedHeight = 反演水位`
- 测试运行命令：`PYTHONPATH="D:/Lib/site-packages" D:/python.exe tests/test_online_sim.py`（`D:/python.exe` 的 site-packages 路径配置损坏，必须显式设 PYTHONPATH，否则 fastapi/rasterio/numpy 不可见）
- 提交信息用中文 `feat:` / `fix:` 前缀

---

### Task 1: 后端 `/api/online_sim` 实时反演接口

**Files:**
- Create: `tests/test_online_sim.py`
- Modify: `app.py`（在 `colorize` 函数之后、`# ---------------- API ----------------` 注释之前插入常量+辅助函数；端点紧跟其后）

**Interfaces:**
- Consumes: `dem/study_dtm.tif`（rasterio 读取，模块级缓存）；`rasterio.features.shapes`
- Produces: `GET /api/online_sim?rain_mm=R` → `{rain_mm, water_level_m, runoff_depth_m, mean_depth_m, max_depth_m, flooded_area_km2, flooded_cells, zones[9], extent: FeatureCollection, note}`；DTM 缺失→404 JSON；参数越界→422

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_online_sim.py`：

```python
# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, r"D:\Competiton")
os.chdir(r"D:\Competiton")
from fastapi.testclient import TestClient
import app

def test_online_sim_consistency_scenarios():
    """在线模拟在 P-III 设计雨量处应与预置重现期场景口径一致(W/面积/分区)。"""
    c = TestClient(app.app)
    scens = {s["return_period_y"]: s for s in c.get("/api/scenarios").json()}
    for T, s in scens.items():
        d = c.get("/api/online_sim", params={"rain_mm": s["rain_mm"]}).json()
        assert abs(d["water_level_m"] - s["water_level_m"]) < 0.06, (T, d["water_level_m"], s["water_level_m"])
        assert abs(d["flooded_area_km2"] - s["flooded_area_km2"]) < 0.05, (T, d["flooded_area_km2"], s["flooded_area_km2"])
        z = c.get("/api/zone_flood", params={"return_period": T, "grid": 3}).json()["zones"]
        for k in range(6):
            assert abs(d["zones"][k] - z[k]) < 1.0, (T, k, d["zones"][k], z[k])
    print("online_sim 与 5 档重现期口径一致 OK")

def test_online_sim_monotonic():
    c = TestClient(app.app)
    prev = (0.0, 0.0, 0)
    for r in (50, 100, 150, 200, 300.6, 500):
        d = c.get("/api/online_sim", params={"rain_mm": r}).json()
        assert d["water_level_m"] >= prev[0] - 1e-6, (r, d["water_level_m"], prev[0])
        assert d["flooded_area_km2"] >= prev[1] - 1e-6, (r, d["flooded_area_km2"], prev[1])
        assert d["flooded_cells"] >= prev[2], (r, d["flooded_cells"], prev[2])
        prev = (d["water_level_m"], d["flooded_area_km2"], d["flooded_cells"])
    print("online_sim 随雨量单调不减 OK (500mm -> W=%.2fm)" % prev[0])

def test_online_sim_extent_in_bbox():
    c = TestClient(app.app)
    d = c.get("/api/online_sim", params={"rain_mm": 250}).json()
    assert d["extent"]["type"] == "FeatureCollection" and len(d["extent"]["features"]) >= 1
    for f in d["extent"]["features"]:
        for pt in f["geometry"]["coordinates"][0]:
            assert 113.30 - 0.01 <= pt[0] <= 113.34 + 0.01, pt
            assert 23.09 - 0.01 <= pt[1] <= 23.13 + 0.01, pt
    print("online_sim extent 在研究区 bbox 内 OK (%d 斑块)" % len(d["extent"]["features"]))

def test_online_sim_small_rain():
    c = TestClient(app.app)
    d = c.get("/api/online_sim", params={"rain_mm": 10}).json()
    assert d["flooded_cells"] < 907, d      # 2年(118.5mm)为 907 格, 10mm 应更少
    assert d["water_level_m"] < 3.11, d
    print("online_sim 小雨量淹没范围小于 2 年档 OK")

def test_online_sim_invalid():
    c = TestClient(app.app)
    assert c.get("/api/online_sim").status_code == 422
    assert c.get("/api/online_sim", params={"rain_mm": -5}).status_code == 422
    assert c.get("/api/online_sim", params={"rain_mm": 3000}).status_code == 422
    print("online_sim 参数校验(缺/负/超2000) 422 OK")

if __name__ == "__main__":
    test_online_sim_consistency_scenarios()
    test_online_sim_monotonic()
    test_online_sim_extent_in_bbox()
    test_online_sim_small_rain()
    test_online_sim_invalid()
    print("test_online_sim OK")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH="D:/Lib/site-packages" D:/python.exe tests/test_online_sim.py
```
预期：第一个测试报 `KeyError: 'water_level_m'`（`/api/online_sim` 不存在，返回 404 JSON `{"detail":"Not Found"}`），后续测试同样失败。

- [ ] **Step 3: 实现后端**

在 `app.py` 的 `colorize()` 函数之后、`# ---------------- API ----------------` 之前插入：

```python
# ==== 在线模拟(自定义雨量 → 浴缸法实时反演, 与重现期场景同口径) ====
RUNOFF_COEF = 0.50      # 综合径流系数(与 bathtub_flood.py 一致)
DEPTH_THRESH = 0.05     # 淹没判定阈值(m)
_dtm_cache = None

def _get_dtm():
    """缓存读去建筑 DTM(study_dtm.tif), 与重现期场景同一份地形。返回 (z, transform)。"""
    global _dtm_cache
    if _dtm_cache is None:
        p = os.path.join(ROOT, "dem", "study_dtm.tif")
        if not os.path.exists(p):
            raise RuntimeError("study_dtm.tif 缺失, 请先运行 bathtub_flood.py")
        with rasterio.open(p) as src:
            z = src.read(1).astype("float32")
            transform = src.transform
        z[np.isnan(z)] = 0.0
        _dtm_cache = (z, transform)
    return _dtm_cache

def _bathtub(z, q):
    """陆域体积守恒: 求 W 使陆域(z>0)平均水深 = q。hi 上限 +q 避免特大暴雨 W 越界。"""
    land = z > 0
    lo, hi = 0.0, float(z.max()) + q
    for _ in range(60):
        W = 0.5 * (lo + hi)
        if float(np.clip(W - z[land], 0, None).mean()) < q:
            lo = W
        else:
            hi = W
    W = 0.5 * (lo + hi)
    return W, np.clip(W - z, 0, None)

def _cell_area_m2(transform, lat=23.11):
    import math
    return (transform.a * 111320.0 * math.cos(math.radians(lat))) * (abs(transform.e) * 110574.0)

def _zone_ratios(z, depth, grid=3):
    """3×3 分区陆地淹没占比(仅陆地, 排除河道), 与 /api/zone_flood 同算法。"""
    land = z > 0
    flood = (depth > 0) & land
    rows, cols = depth.shape
    rstep, cstep = max(1, rows // grid), max(1, cols // grid)
    zones = []
    for i in range(grid):
        rlo, rhi = i * rstep, min((i + 1) * rstep, rows)
        for j in range(grid):
            clo, chi = j * cstep, min((j + 1) * cstep, cols)
            blk_f = flood[rlo:rhi, clo:chi]
            blk_l = land[rlo:rhi, clo:chi]
            nl = int(blk_l.sum())
            zones.append(round(100.0 * (int(blk_f.sum()) / nl) if nl else 0.0, 1))
    return zones

@app.get("/api/online_sim")
def online_sim(rain_mm: float = Query(..., gt=0, le=2000)):
    """在线模拟: 输入 24h 雨量(mm) → 浴缸法实时反演水位/淹没范围/分区占比。"""
    try:
        z, transform = _get_dtm()
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    q = rain_mm / 1000.0 * RUNOFF_COEF
    W, depth = _bathtub(z, q)
    land = z > 0
    flooded = (depth > DEPTH_THRESH) & land
    feats = []
    if flooded.any():
        from rasterio.features import shapes
        for g, v in shapes(flooded.astype("uint8"), mask=flooded, transform=transform):
            if v == 1:
                feats.append({"type": "Feature", "geometry": g, "properties": {}})
    area = _cell_area_m2(transform)
    return {
        "rain_mm": rain_mm,
        "water_level_m": round(float(W), 2),
        "runoff_depth_m": round(q, 4),
        "mean_depth_m": round(float(depth[flooded].mean()), 2) if flooded.any() else 0.0,
        "max_depth_m": round(float(depth[flooded].max()), 2) if flooded.any() else 0.0,
        "flooded_area_km2": round(float(flooded.sum() * area) / 1e6, 3),
        "flooded_cells": int(flooded.sum()),
        "zones": _zone_ratios(z, depth, 3),
        "extent": {"type": "FeatureCollection", "features": feats},
        "note": "浴缸法实时反演(与重现期场景同口径)",
    }
```

- [ ] **Step 4: 运行测试确认通过**

```bash
PYTHONPATH="D:/Lib/site-packages" D:/python.exe tests/test_online_sim.py
```
预期：5 个测试全 PASS，输出含 `online_sim 与 5 档重现期口径一致 OK`。

- [ ] **Step 5: 提交**

```bash
git add app.py tests/test_online_sim.py
git commit -m "feat: /api/online_sim 在线模拟接口(输入24h雨量→浴缸法实时反演水位/淹没范围/分区占比, 与重现期同口径)"
```

---

### Task 2: 前端「在线模拟」模式

**Files:**
- Modify: `tests/test_online_sim.py`（追加 `test_index_html_online_mode`）
- Modify: `index.html`（CSS、模式按钮、onlineSection、setMode 扩展、runOnline、note 文本）

**Interfaces:**
- Consumes: `/api/online_sim` 返回值；复用 `showLoading`/`hideLoading`、`updateZones`、`waterEntities`
- Produces: `data-mode="online"` 按钮、`#onlineSection`（`#rainInput` + `#onlineRun` + `#ostats` + `#onote`）、`runOnline()`、`setMode('online')` 支持

- [ ] **Step 1: 追加前端失败测试**

在 `tests/test_online_sim.py` 的 `if __name__ == "__main__":` 之前追加：

```python
def test_index_html_online_mode():
    html = open("index.html", encoding="utf-8").read()
    assert 'data-mode="online"' in html, "缺在线模拟模式按钮"
    assert 'onlineSection' in html and 'rainInput' in html and 'onlineRun' in html, "缺在线模拟区块"
    assert '/api/online_sim' in html and 'runOnline' in html and 'isOnline' in html, "缺在线模拟逻辑"
    print("index.html 在线模拟代码存在 OK")
```

并把 `__main__` 块改为：

```python
if __name__ == "__main__":
    test_online_sim_consistency_scenarios()
    test_online_sim_monotonic()
    test_online_sim_extent_in_bbox()
    test_online_sim_small_rain()
    test_online_sim_invalid()
    test_index_html_online_mode()
    print("test_online_sim OK")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
PYTHONPATH="D:/Lib/site-packages" D:/python.exe tests/test_online_sim.py
```
预期：后端 5 测试 PASS，最后 `test_index_html_online_mode` 断言 `data-mode="online"` FAIL（index.html 尚未修改）。

- [ ] **Step 3: index.html — CSS**

在 `.compare b { color: #4fc3f7; }` 行之后插入：

```css
    /* ===== 在线模拟区块 ===== */
    .online-row { display: flex; gap: 6px; margin-bottom: 10px; }
    #rainInput { flex: 1; min-width: 0; padding: 8px 10px; border: 1px solid rgba(79,195,247,0.4);
      background: rgba(13,25,43,0.6); color: #e6f1ff; border-radius: 8px; font-size: 13px; }
    #rainInput:focus { outline: none; border-color: #4fc3f7; }
    #onlineRun { flex: 0 0 auto; padding: 8px 14px; border: 1px solid rgba(79,195,247,0.5);
      background: #0288d1; color: #fff; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; }
    #onlineRun:hover { background: #039be5; }
    .onote { font-size: 11px; color: #6b8299; margin-top: 8px; line-height: 1.5; }
```

- [ ] **Step 4: index.html — 模式按钮**

把

```html
      <div class="modebtn" data-mode="real">真实 · 英德</div>
```

改为（追加在线模拟按钮）：

```html
      <div class="modebtn" data-mode="real">真实 · 英德</div>
      <div class="modebtn" data-mode="online">在线模拟</div>
```

- [ ] **Step 5: index.html — onlineSection 区块**

把

```html
    <div id="simSection">
      <div class="row" id="btns"></div>
      <div class="stats" id="stats"></div>
    </div>

    <div id="realSection" class="hidden">
```

改为（在 simSection 与 realSection 之间插入 onlineSection）：

```html
    <div id="simSection">
      <div class="row" id="btns"></div>
      <div class="stats" id="stats"></div>
    </div>

    <div id="onlineSection" class="hidden">
      <div class="online-row">
        <input id="rainInput" type="number" min="10" max="2000" step="5" placeholder="24h雨量(mm)" />
        <div id="onlineRun">开始模拟</div>
      </div>
      <div class="stats" id="ostats"></div>
      <div class="onote" id="onote">浴缸法实时反演(与重现期场景同口径)：雨量 → 径流深 → 水位W → 水深。118.5–300.6mm 为 2–100 年设计范围，超出为外推。</div>
    </div>

    <div id="realSection" class="hidden">
```

- [ ] **Step 6: index.html — setMode 扩展**

把

```js
    function setMode(m) {
      var isReal = (m === 'real');
      document.getElementById('simSection').classList.toggle('hidden', isReal);
      document.getElementById('realSection').classList.toggle('hidden', !isReal);
```

改为：

```js
    function setMode(m) {
      var isReal = (m === 'real');
      var isOnline = (m === 'online');
      var showSim = (m === 'sim');
      document.getElementById('simSection').classList.toggle('hidden', !showSim);
      document.getElementById('realSection').classList.toggle('hidden', !isReal);
      document.getElementById('onlineSection').classList.toggle('hidden', !isOnline);
```

再在 setMode 函数末尾（`document.querySelectorAll('.modebtn').forEach(...)` 那一行之后、函数闭合 `}` 之前）追加：

```js
      if (showSim && typeof curT === 'number') select(curT);   // 回到模拟模式时重建当前重现期水体(避免残留在线模拟结果)
```

- [ ] **Step 7: index.html — runOnline 逻辑**

把

```js
    var curBtn = null;
    function select(T) {
      var s = SCENARIOS.find(function(x) { return x.return_period_y === T; });
      showLoading();                 // 切换重现期 → 显示 "unet 正在反演中" 加载遮罩
      renderStats(s); setWater(T, s);
      fetchZones(T);
    }
```

改为（记录当前重现期 + 新增在线模拟逻辑）：

```js
    var curBtn = null;
    var curT = null;
    function select(T) {
      curT = T;
      var s = SCENARIOS.find(function(x) { return x.return_period_y === T; });
      showLoading();                 // 切换重现期 → 显示 "unet 正在反演中" 加载遮罩
      renderStats(s); setWater(T, s);
      fetchZones(T);
    }

    // ===== 在线模拟: 用户输入 24h 雨量 → 后端浴缸法实时反演 =====
    var onlineSeq = 0;
    function renderOnlineStats(d) {
      document.getElementById('ostats').innerHTML =
        '<div class="stat"><span class="k">雨量</span><span class="v">' + d.rain_mm + ' mm</span></div>' +
        '<div class="stat"><span class="k">反演水位</span><span class="v">' + d.water_level_m + ' m</span></div>' +
        '<div class="stat"><span class="k">径流深</span><span class="v">' + d.runoff_depth_m + ' m</span></div>' +
        '<div class="stat"><span class="k">淹没面积</span><span class="v">' + d.flooded_area_km2 + ' km²</span></div>' +
        '<div class="stat"><span class="k">平均水深</span><span class="v">' + d.mean_depth_m + ' m</span></div>' +
        '<div class="stat"><span class="k">最大水深</span><span class="v">' + d.max_depth_m + ' m</span></div>';
    }
    function runOnline() {
      var v = parseFloat(document.getElementById('rainInput').value);
      if (!(v > 0) || v < 10 || v > 2000) {
        document.getElementById('status').innerHTML = '⚠ 请输入 10–2000mm 之间的 24h 雨量';
        return;
      }
      var seq = ++onlineSeq;
      showLoading();                       // 复用 "Unet正在反演中" 加载遮罩
      fetch('/api/online_sim?rain_mm=' + v).then(function(r) { return r.json(); }).then(function(d) {
        if (seq !== onlineSeq) return;     // 丢弃被更新的过期响应
        if (d.error) throw new Error(d.error);
        waterEntities.forEach(function(e) { viewer.entities.remove(e); });
        waterEntities = [];
        (d.extent.features || []).forEach(function(f) {
          var ring = f.geometry.coordinates[0];
          var pos = []; ring.forEach(function(p) { pos.push(p[0], p[1]); });
          waterEntities.push(viewer.entities.add({
            polygon: {
              hierarchy: Cesium.Cartesian3.fromDegreesArray(pos),
              height: 0, extrudedHeight: d.water_level_m,
              material: Cesium.Color.fromCssColorString('#2196f3').withAlpha(0.5)
            }
          }));
        });
        updateZones(d.zones);
        renderOnlineStats(d);
        document.getElementById('status').innerHTML =
          '在线模拟完成: ' + v + 'mm → 水位 ' + d.water_level_m + 'm, ' + (d.extent.features || []).length + ' 个淹没斑块';
        hideLoading();
      }).catch(function(e) {
        if (seq !== onlineSeq) return;
        document.getElementById('status').innerHTML = '⚠ 在线模拟失败: ' + ((e && e.message) || '网络错误');
        hideLoading();
      });
    }
    document.getElementById('onlineRun').onclick = runOnline;
    document.getElementById('rainInput').addEventListener('keydown', function(ev) {
      if (ev.key === 'Enter') runOnline();
    });
```

- [ ] **Step 8: index.html — 更新底部说明**

把

```html
    <div class="note">模拟:Gumbel 重现期 → 浴缸法反演水位 → 水深。真实事件:卫星影像 → UNet 水体提取 → 边界水位反演水深。数据由服务层 API 提供。仅供演示。</div>
```

改为：

```html
    <div class="note">模拟:P-III 重现期 / 在线自定义雨量 → 浴缸法反演水位 → 水深。真实事件:卫星影像 → UNet 水体提取 → 边界水位反演水深。数据由服务层 API 提供。仅供演示。</div>
```

- [ ] **Step 9: 运行测试确认通过**

```bash
PYTHONPATH="D:/Lib/site-packages" D:/python.exe tests/test_online_sim.py
```
预期：全部 6 个测试 PASS。

- [ ] **Step 10: 提交**

```bash
git add index.html tests/test_online_sim.py
git commit -m "feat: 三维模拟新增在线模拟模式(输入24h雨量→实时反演三维水体+分区标签+统计)"
```

---

### Task 3: 全量回归验证

**Files:** 无代码改动，仅验证。

- [ ] **Step 1: 运行全部测试套件**

```bash
cd /d/Competiton && export PYTHONPATH="D:/Lib/site-packages" && for f in tests/test_*.py; do echo "== $f =="; D:/python.exe "$f" || echo "FAIL: $f"; done
```
预期：除 `test_sat_data.py`（依赖外部网络，可能偶发失败，重试即可）外全部 PASS；`test_zone_flood.py`、`test_api_fallback.py`、`test_api_realevent.py` 必须 PASS（确认在线模拟未破坏既有口径与前端引用）。

- [ ] **Step 2: 手动冒烟（可选）**

```bash
cd /d/Competiton && PYTHONPATH="D:/Lib/site-packages" D:/python.exe -c "
from fastapi.testclient import TestClient
import app
c = TestClient(app.app)
for r in (60, 118.5, 200, 300.6, 500):
    d = c.get('/api/online_sim', params={'rain_mm': r}).json()
    print(r, 'mm -> W=%.2fm area=%.3fkm2 zones=%s' % (d['water_level_m'], d['flooded_area_km2'], d['zones'][:6]))
"
```
预期：雨量增大 → W/面积/分区占比单调上升；118.5mm 处 ≈ 2 年档（W≈3.11）。

- [ ] **Step 3: 提交**

```bash
git add -A
git status
```
若无新改动则跳过提交；若有（如测试微调），提交：

```bash
git commit -m "fix: 在线模拟全量回归验证"
```
