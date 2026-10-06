# CHANGELOG

## [1.4] — 2026-10-06

### 仓库工程化整理(文件分类)
- `pipeline/` ← 15 个数据管线脚本(prep_*/fetch_*/bathtub/load_flood_pg/export_*/
  train/eval/infer/realevent_beijiang); 统一 ROOT=仓库根 + sys.path 引导, README 命令同步
- `web/data/` ← 12 个前端生成数据(*_data.js / buildings_3d*.js / flood_*.js /
  flood_depth_*y.png / unet_samples.js); 页面引用与导出脚本写出路径同步
- `deploy/` ← watchdog.bat / load_buildings.sql; `docs/` ← RUNTIME_CHECKLIST / WORKFLOW
- `data_raw/` ← 7 个零引用中间数据(gz_buildings*.json 等)移出 git 跟踪(保留本地)
- 根目录只保留服务层模块/共享库/前端入口页/工程约定文件
- sync_delivery 同步集升级为"git 跟踪全集 ∪ 静态清单", 守护测试改为逐文件完整性核查;
  test_local_resources 扩展为 11 页"页面本地引用逐一存在"断链守护

## [1.3] — 2026-10-06

### 安全(全量代码审计修复)
- Cesium Ion token 历史泄露确认 → 交付包/历史核查流程固化(轮换由用户在 Ion 控制台执行)
- `web/config.local.js` 凭据从交付副本强制移除(sync_delivery.py DENY 黑名单 + 守护测试)
- 登录接口爆破防护: IP+用户名双键限流(5 次/分钟, 此前 60 次错口令无一 429)
- `/api/predict` 流式截断读取(64MB 上限不再全量进内存) + 5 次/分钟频控 + 非 GeoTIFF 归 400
- `/api/subscribe` 加固: 3 次/分钟限流 + 总量上限 409 + 白名单式邮箱校验 + 入库剥标签
- token 支持 `Authorization: Bearer`(query 兼容); uvicorn 访问日志 `?token=` 自动打码
- 报讯写盘故障(OSError/磁盘满/文件占用)归 503, 不再伪装成"字段格式错"400

### 修复
- `prep_design_storm.py` NameError(RETURNS 收拢后未跟进), README 标注"必跑"的脚本恢复可复现
- 频控 `_rate_ok` 真正持 `_rate_lock`(check-then-act 原子性)

### 一致性(ARC-01)
- 淹没阈值 0.05 三处硬编码/面积魔数五处/colorize 双份实现全部收拢:
  `pipeline_config`(DEPTH_BINS/cell_area_m2) + `flood_render.py`(色带唯一实现)
- XYZ 瓦片脚本(tools/tile_flood.py)自持的 12 行色带渐变同样接入 flood_render(ARC-01d)

### 工程化
- sync_delivery.py 升级: 根级文件改为"静态清单 ∪ git 跟踪根级文件 ∪ flood_depth_*y.png"
  (DEL-04: 水深 PNG 曾漏出清单 → 交付版 flood.html 水深图层全 404); web/ 全目录纳入同步
  (echarts.min.js 曾不在任何清单); 数据目录(dem/flood_out/unet_out/realevent_out)一并同步
  使副本可独立运行, 凭据黑名单强制移除; 交付守护测试升级(能出数 200/无凭据/资产完整性/zip 新鲜度)
- CI: 先重建交付副本再跑测试(守护不再空转) + ruff E9,F 门禁 + junit/依赖冻结归档
- 依赖升级: pillow 12.3.0 / python-multipart 0.0.32(OSV HIGH 修复版)
- 死导入清理 15 处 + `_reports_file` 重复定义删除

### 测试
- 新增 test_audit_regressions.py(写盘 503/登录限流/订阅加固/predict 400/日志打码/影子常量扫描等)
- test_deploy_files.py 重写(交付副本四项守护 + zip 新鲜度断言)

## [1.2] — 2026-10-05

### 安全加固
- 存储型 XSS 双层消毒: 后端 `_clean_text` 剥标签 + 前端 `esc()` 转义
- 匿名上报 IP 频控(20 条/60 秒, `FLOOD_TRUST_PROXY` 开关控制 XFF 信任)
- 凭据外置: 天地图 key 与 Ion token 移入 gitignore 的 `web/config.local.js`
- 静态文件 basename 黑名单(web_users/subscribers/reports.json 一律 404)
- `redirect_slashes=False` 消除 `//` 路径兜底链
- `torch.load` 加 `weights_only=True` 防 pickle 注入

### 工程化
- 淹没水深预渲染 XYZ 瓦片服务(`tools/tile_flood.py`, z9–14)
- 重现期情景 ±20% 降雨敏感性不确定性带
- Copernicus EMS 模板三件套专题图(extent/facilities/hazard)
- UNet 演示样本改取 `val_scenes`(未参与训练), 精度卡含 by_region 分区
- `_Memo` 统一 10 个缓存的查算存(带锁防 dogpile + FIFO 淘汰)
- 报讯 store 读写加锁 + 原子写(tmp+os.replace) + 损坏隔离(.corrupt-ts)
- `/api/health` v2: db_reachable/scenarios_source/artifacts/cache 命中率/store_quarantines
- 底图懒兜底: 天地图 4s 探测失败才加 Ion(省 60+ 首屏外网请求)
- getJSON 统一 r.ok 检查 + 15s 超时(AbortController)
- pipeline_config.py 收拢全仓常量(bbox/DEPTH_CAP/RETURNS 等 7+ 处)

### 数据修正
- 双事件(英德/梅州) NEAREST 重跑: 云量真值(22.3%/61.6%), SAR 变化检测修复
- UNet 演示样本改取验证场景(val_scenes), 消除训练集泄漏
- reports.json 清理至 4 条真实/演示记录(删 53 条测试残留)
- train_unet 保存 val_IoU 最优权重(旧版无条件存最后一个 epoch)

### 测试
- 新增 test_zero_coverage.py(health/png/critical_assets/predict/subscribe)
- 新增并发上报 10 线程不丢不重测试
- 新增凭据外置检查(无内嵌 key/JWT, config.local.js 未跟踪)
- pytest.ini: network marker 默认过滤(日常回归约 10s, 外网链路 opt-in)
- store 隔离: conftest.py autouse fixture 重定向到 tmp_path

## [1.1] — 2026-10-04

### 功能
- 分级预警条 / 疏散分析(A* 避水寻路) / 防汛智能问答 / 专题图导出 / 实时雨情
- 公众报汛(移动端 H5) / 用户登录(管理员/公众) / 情景双屏对比 / GeoScene 2D 分析

## [1.0] — 2026-09

### 初始版本
- 三维洪涝模拟 / 数据大屏 / 真实事件反演(英德) / UNet 水体提取
- 浴缸法水位反演 / P-III 设计暴雨 / Gumbel 重现期拟合
