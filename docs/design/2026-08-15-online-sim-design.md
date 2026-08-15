# 在线模拟（自定义雨量 → 浴缸法实时反演淹没程度）设计

日期：2026-08-15
状态：已评审通过，进入实现

## 背景与目标

现三维模拟只有 5 个预置重现期档位（2/5/10/50/100 年，对应 P-III 24h 设计暴雨 118.5–300.6mm），用户无法输入任意雨量。

本次新增「在线模拟」模式：用户输入任意 24h 降水量（mm），系统用与预置档位**完全同口径**的浴缸法实时反演，得到淹没范围、水位、水深统计与分区淹没占比，并在三维场景中渲染。

用户已确认采用 **后端浴缸法实时反演**（非插值查表）。

## 方法（与 bathtub_flood.py 同口径）

现有管线（bathtub_flood.py）：
- 径流深 `q = R/1000 × 0.50`（R=24h 雨量 mm，综合径流系数 0.5）
- 陆域体积守恒二分求水位 W：`mean(max(0, W − z)) = q`，仅对陆域 `z>0`
- 水深 `depth = max(0, W − z)`；新淹没范围 = `depth>0.05 且 z>0`（排除珠江河道）
- `dem/study_dtm.tif` 已是去建筑 DTM，与重现期场景用的是同一份地形

在线模拟直接复用该流程，仅把 R 换成用户输入值。唯一改动：二分上限由 `z.max()` 放宽到 `z.max()+q`，避免超大暴雨 W 越界；常规雨量结果与 5 档完全一致（测试中验证）。

## 架构

### 后端 `app.py` — 新增 `GET /api/online_sim?rain_mm=R`

参数：`rain_mm: float = Query(..., gt=0, le=2000)`（越界自动 422）。

计算流程：
1. 模块级缓存 `_get_dtm()`：首次读 `dem/study_dtm.tif` → `(z, transform)`，`z[np.isnan(z)]=0`
2. `q = rain_mm/1000 × 0.50`；浴缸法求 W、depth（60 次二分，~150×150 栅格，毫秒级）
3. 淹没掩膜 `(depth>0.05) & (z>0)` → `rasterio.features.shapes` 多边形 → extent GeoJSON
4. 分区占比 `zones`：grid=3，与 `/api/zone_flood` 相同算法（仅陆地淹没占陆地之比）
5. 统计：`flooded_area_km2`、`mean_depth_m`、`max_depth_m`、`flooded_cells`

返回 JSON：

```json
{
  "rain_mm": 200, "water_level_m": 3.76, "runoff_depth_m": 0.10,
  "mean_depth_m": 1.33, "max_depth_m": 3.76,
  "flooded_area_km2": 1.20, "flooded_cells": 1362,
  "zones": [0.0, ...9个值...],
  "extent": {"type": "FeatureCollection", "features": [...]},
  "note": "浴缸法实时反演，与重现期场景同口径"
}
```

错误处理：DTM 缺失 → 404 JSON；雨量越界 → 422；小雨量无淹没 → 空 extent + 面积 0，仍 200。

### 前端 `index.html`

1. `.modes` 新增按钮 `<div class="modebtn" data-mode="online">在线模拟</div>`
2. 新增 `#onlineSection`（默认 hidden）：
   - 雨量输入框 `#rainInput`（number，min=10，max=2000，step=5，placeholder "24h雨量(mm)"）
   - 「开始模拟」按钮 `#onlineRun`
   - 统计面板 `#ostats`（复用 `.stats`/`.stat` 样式）
   - 方法说明 `#onote`（含"118.5–300.6mm 为 2–100 年设计范围，超出为外推"）
3. `runOnline()`：
   - 校验输入（非空、10–2000），否则状态栏提示并返回
   - `showLoading()`（复用 "Unet正在反演中" 遮罩）→ fetch `/api/online_sim?rain_mm=V`
   - 成功：清空并重建 `waterEntities`（用 `d.extent.features`，`extrudedHeight=d.water_level_m`，材质 `#2196f3` alpha 0.5，与模拟模式一致）→ `updateZones(d.zones)` → `renderOnlineStats(d)` → `hideLoading()`
   - 失败：状态栏报错 + `hideLoading()`
   - 请求序号守卫（`onlineSeq`），丢弃过期响应
4. `setMode` 扩展：`online` 模式下显示 `#onlineSection`；水实体在 `sim` 与 `online` 都显示、`real` 隐藏；`zoneOverlay` 在 `real` 隐藏、其余显示
5. 底部 `.note` 注释更新为与新方法一致（去掉过时的 "Gumbel 重现期" 表述）

## 数据流

```
用户在在线模式输入 R → 点击开始模拟
  → GET /api/online_sim?rain_mm=R
  → 后端: 缓存DTM → 浴缸法 W/depth → 淹没多边形+分区占比+统计
  → 前端: 重建三维水体(水位W) + 更新分区标签 + 渲染统计
```

## 测试（TDD，`tests/test_online_sim.py`）

- `test_online_sim_consistency_2y`：`rain_mm=118.5` → `water_level_m≈3.11(±0.06)`、`flooded_area_km2≈0.792(±0.05)`、`zones[0..5]` 与 `/api/zone_flood(2y)` 相差 <1.0 —— 证明与预置档位口径一致
- `test_online_sim_monotonic`：雨量增大 → W/面积/淹没格网单调不减；`300.6mm` → W≈4.31(±0.06)
- `test_online_sim_extent_in_bbox`：返回多边形均位于研究区 bbox 内
- `test_online_sim_small_rain`：`rain_mm=10` → 仍 200，面积≈0
- `test_online_sim_invalid`：缺参数 / 负值 / `>2000` → 422
- `test_index_html_online_mode`：index.html 含 `data-mode="online"`、`onlineSection`、`/api/online_sim`、`runOnline`

## 涉及文件

- `app.py`（新增 `/api/online_sim`、DTM 缓存、内联浴缸法）
- `index.html`（模式按钮、onlineSection、runOnline、setMode 扩展、note 更新）
- `tests/test_online_sim.py`（新增）
