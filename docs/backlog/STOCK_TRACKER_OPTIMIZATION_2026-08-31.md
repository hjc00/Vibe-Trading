# A股追踪（stock_tracker）优化待办

> 分析时间：2026-08-31 ｜ 最后更新：2026-08-31
>
> 高优先级 3 项已修复（见文末「已修复」）；本文档记录**尚未处理**的中、低优先级问题，随项目代码一起维护。

---

## 中优先级

### 1. `get_latest_snapshot` 全量加载最多 100 份快照
- 位置：`agent/src/stock_tracker/store.py:100-106`
- 现状：为取「最新」快照，`list_snapshots(limit=100)` 逐个完整反序列化 + Pydantic 校验，再比较 `generated_at`。
- 建议：`list_snapshot_dates` 已按文件名日期降序排序，直接用最新日期 `get_snapshot` 即可；或写入时维护 `latest.json` 指针。

### 2. 股票名称解析无缓存
- 位置：`agent/src/stock_tracker/engine.py:67-71`、`agent/src/stock_tracker/names.py`
- 现状：每次 refresh 都调用 `fetch_a_share_names` 请求腾讯 quote API（15s 超时），名称极少变化。
- 建议：加 TTL/模块级缓存，refresh 时仅对缺失代码增量拉取。

### 3. LLM 无缓存 + 每次重建 ChatLLM
- 位置：`agent/src/stock_tracker/analyzer.py:175`
- 现状：每次 `run_analysis` 都 new 一个 `ChatLLM()`（`build_llm` 初始化开销不小），无结果缓存、未显式传 timeout。
- 建议：复用 ChatLLM 实例；对「trading_date + focus + symbol 集合」做结果缓存；显式传 timeout。

### 4. 分析历史 N+1 读取
- 位置：`agent/src/stock_tracker/store.py:175-198`
- 现状：`list_analyses` 对每个 id 重读整份 JSON 只为取 summary；`get_latest_analysis` 同理。
- 建议：分析 id 本身是 `%Y%m%dT%H%M%S%f` 时间戳，天然有序，按文件名排序取最新即可；或维护轻量 index。

### 5. 前端刷新流程冗余
- 位置：`frontend/src/pages/StockTracker.tsx:71-81`
- 现状：`refresh()` 先 `await api.refreshStockTracker()`（该接口阻塞返回时刷新已完成），随后 `pollRefreshStatus()` 又多打一次 status + 一次 getSnapshot。
- 建议：去除冗余轮询，或在后端改为非阻塞刷新后再保留轮询。

---

## 低优先级 / 正确性提示

### 6. 硬编码 `period="10"` 做跨日 diff
- 位置：`agent/src/stock_tracker/engine.py:367-368`
- 现状：`_compute_diff_map` 只比较 `period_signals["10"]`，若用户配置 periods 不含 10（模型允许 1-250），diff 会静默为空。
- 建议：改为取最短周期或显式第一周期。

### 7. 死代码 `_set_refresh_progress`
- 位置：`agent/src/api/stock_tracker_routes.py:153-159`
- 现状：定义了但从未被调用，`_REFRESH_STATE["symbols"]` 恒为空。
- 建议：删除，或真正接入 per-symbol 进度回报。

### 8. 双端归一化逻辑重复
- 位置：`frontend/src/lib/stockTracker.ts:54-70` 与后端 `agent/src/stock_tracker/models.py:202-232`
- 现状：`normalizeAShareCode` / `inferAShareExchange` 两端各维护一套，规则调整需同步改两处。
- 建议：统一为单一权威来源（或至少加注释指向彼此、补前端单测）。

### 9. 数据层遗留与不一致
- `data/stock_tracker/analysis.json`：refactor 前遗留的旧格式文件（根目录、无 `id`），`list_analyses` 只扫 `analyses/` 目录，读不到它，属死文件。
- `settings.json` 里 `breakout_window: 20.0`（float）与快照里的 `20`（int）不一致，数据不整洁。

### 10. 后端微优化
- `_REFRESH_STATE` 双锁 + 每次 `deepcopy`（`agent/src/api/stock_tracker_routes.py:28-35`）。
- `_get_store` check-then-set 无锁（`agent/src/api/stock_tracker_routes.py:40-45`）。
- `compute_mas` 内 `df.copy()`（`agent/src/stock_tracker/signals.py:41`）非必需。

---

## 已修复（2026-08-31）

| 问题 | 文件 |
|---|---|
| `POST /refresh` 阻塞事件循环 → `asyncio.to_thread` | `agent/src/api/stock_tracker_routes.py:300` |
| 前端选中行触发全量重拉 → `loadSnapshot` 去 `selectedCode` 依赖 | `frontend/src/pages/StockTracker.tsx:47-60` |
| 1s 轮询 + `setInterval` 无卸载清理 → 降到 2s + ref 清理 | `frontend/src/pages/StockTracker.tsx` |
