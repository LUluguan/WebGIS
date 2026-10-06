# 并行工作线隔离约定

## 问题
多条工作线(编码 + 审计 + 手动验证)在同一目录同一批文件上并行写, 已产生三次时序错位:
1. 交付副本 index.html 落后根目录 6 行(fj 超时)
2. 交付副本被旧版覆盖(CORS 回归)
3. pytest.ini 在审计"第一次检查不存在、五分钟后存在且生效"

## 约定(按优先级)
1. **编码线**: 在主目录 D:\Competiton 直接操作, 但每次"完成一批"后立即:
   - `python tools/sync_delivery.py` (交付副本同步)
   - `git add -A && git commit` (工作区快照)
   - `git push origin main` (经确认后)
2. **审计线**: 基于 `git show HEAD:文件名` 或 `git archive` 取快照审计, **不要**直接读工作区文件(中间状态不可靠)。
3. **验证线**: 浏览器验证前先 `curl /api/health` 确认服务在跑且版本匹配, 验证完立即记录结果。

## Worktree 方案(如需真正隔离)
```bash
git worktree add ../WebGIS-audit HEAD    # 审计用独立副本
cd ../WebGIS-audit && pip install -r requirements.txt && pytest -q
# 审计结论出后再切回主目录
```
