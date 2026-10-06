# RUNTIME_CHECKLIST — 部署/评审机自检

## 交付副本(开发源码目录)运行前提
代码副本与数据分目录交付时, 服务端按 `代码目录/dem|flood_out|unet_out|realevent_out` 解析路径:
- **方式一(推荐)**: 运行 `python tools/sync_delivery.py`——自动同步代码并把上述四类数据
  一并拷入副本(STAC 缓存 `_cache.npz` 除外), 同时强制移除 `web/config.local.js` 等凭据文件;
- **方式二(手工)**: 把 `C2132_相关数据/` 下的 `dem`、`flood_out`、`unet_out`、`realevent_out`
  复制到本目录, 再启动服务。
缺数据时首页可打开, 但 `/api/scenarios`、`/api/impact` 等核心端点返回 404(2026-10 审计 DEL-02)。

## 必须随包附带(不可再生, 缺失即功能缺失)
| 项 | 用途 | 缺失表现 |
| --- | --- | --- |
| dem/study_dtm.tif | 浴缸法地形 | 淹没端点 404 `{"error":"无 100 年水深栅格"}` |
| flood_out/scenarios.json + flood_depth_*.tif + flood_extent_*.geojson | 情景数据 | 场景/水体 404 |
| flood_out/design_storm_24h.json | 在线模拟雨量 | 在线模拟外推 |
| unet_out/unet_water.pt | UNet 推理 | /api/predict 503 |
| unet_out/samples/ + eval_metrics.json | 精度卡 | unet/dashboard 精度卡空 |
| web/ (Cesium+ECharts) | 前端 | 页面白屏 |
| web/config.local.js | 天地图/Ion key | 底图走兜底或无底图(凭据文件, 禁入交付包, 部署时按 config.example.js 自备) |
| realevent_out/ (yingde/meizhou) | 真实事件 | 真实事件页空 |

## 可再生(管线脚本, 见 README 数据管线)
precip_tif/(pipeline/prep_precip.py) · flood_tiles/(tools/tile_flood.py) · uncertainty.json(tools/uncertainty_bands.py) · dem/study_pop.tif(pipeline/fetch_pop.py, 需 4.6GB 下载)

## 仓库布局(2026-10 工程化整理)
`pipeline/` 数据管线脚本 · `web/data/` 前端生成数据 · `deploy/` 部署辅助 · `docs/` 文档
(本清单随之位于 docs/; 其余不变)

## 提交包自检(2026-10 审计 DEL-01/03)
- 交付包内**不得**含 `web/config.local.js`、`.env`、`web_users.json`(凭据);
- `C2132_作品.zip` 修改时间必须晚于最后一次提交(`pytest -k zip` 有断言守护), 单一 zip,
  不用分卷(Windows 资源管理器无法双击解压分卷包)。

## 自检命令
```
curl http://127.0.0.1:8001/api/health   # artifacts 全 true 即关键产物齐
pytest -q                               # 全量离线用例应全绿(唯一例外: C2132_作品.zip
                                        #   未重打包时 zip 新鲜度断言为预期红)
```
