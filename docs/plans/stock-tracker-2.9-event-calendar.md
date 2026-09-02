# stock-tracker 2.9 事件与日历集成 —— 详细方案

> 归属：[stock-tracker-improvement-plan.md](stock-tracker-improvement-plan.md) 中 2.9「事件与日历集成」的完整落地设计。
> 创建时间：2026-09-02
> 状态：**已实施**（Phase A + Phase B 均已落地，2026-09-02）
> 落地要点：东财 `RPT_LIFT_STOCK` 实际不填充 `FREE_RATIO`/`LIFT_MARKET_CAP` 字段，
> 真实解禁比例字段为 `ADD_LISTSHARES_RATIO`（占总股本 0–1 小数），`fetch_lockup_records`
> 以 ALL 列取回并按 0–1 小数存 `free_ratio`；龙虎榜 datacenter 不支持日期区间过滤，
> `fetch_recent_board` 按天回扫近 5 个有榜的交易日。默认 `detail_card_count` 由 5 提到 6
> （`DETAIL_CARD_COMPONENTS` 现含 6 张卡，事件卡默认可见），并同步已持久化 settings 与测试。

---

## 0. 一句话结论

新增 `agent/src/stock_tracker/events_data.py`，聚合 **限售解禁 / 业绩预告 / 龙虎榜 / 股东增减持** 四类事件，产出 `EventSnapshot`（含综合事件风险分 `event_risk_score` 0–100），挂到 `SymbolSnapshot.events`，前端加一张 `EventTimelineCard` 详情卡（未来 90 天事件时间线 + 高危标红），并把事件风险注入 LLM 分析 prompt。

**解禁与龙虎榜直接复用现有东财工具（零新数据源风险），业绩预告与增减持走 Tushare 兜底（best-effort，缺失不阻塞主流程）。**

---

## 1. 现状与可复用基础（已核对源码）

| 事件类型 | 现有可复用资产 | 数据源 | 是否需新代码 |
|---|---|---|---|
| 限售解禁 | [lockup_expiry_tool.py](../../agent/src/tools/lockup_expiry_tool.py) `get_lockup_expiry(code, horizon)` | 东财 `RPT_LIFT_STOCK` | 仅需暴露一个公开纯函数 |
| 龙虎榜 | [dragon_tiger_tool.py](../../agent/src/tools/dragon_tiger_tool.py) `get_dragon_tiger(date, code)` | 东财 `RPT_DAILYBILLBOARD_DETAILS`（+ tushare `fetch_dragon_tiger` 兜底） | 仅需一个公开查询函数 |
| 业绩预告 | [tushare_fallbacks.py](../../agent/src/tools/tushare_fallbacks.py)（**尚无** `forecast`） | Tushare `forecast`（需 2000 积分） | 新增 `fetch_forecast` |
| 股东增减持 | 同上（**尚无** `stk_holdertrade`） | Tushare `stk_holdertrade`（需 2000 积分） | 新增 `fetch_holder_trade` |

关键结论：
- **解禁 + 龙虎榜是 P0 主线**，东财免费接口已稳定可用（这正是 2.9 计划里「优先接入财报日历与解禁预警」的落点）。
- 业绩预告 / 增减持依赖 Tushare 积分，作为 **P1 可选增强**，`TUSHARE_TOKEN` 缺失或积分不足时按 symbol 记 error、不影响主流程——与 `valuation_data.py` 的降级策略完全一致。

---

## 2. 后端设计

### 2.1 数据模型（`agent/src/stock_tracker/models.py` 新增）

```python
class EventItem(BaseModel):
    """One upcoming/recent corporate event for a symbol."""
    event_type: str = ""          # lockup | earnings_forecast | dragon_tiger | holder_trade
    event_date: Optional[date] = None
    title: str = ""               # 中文短标题，如「解禁 1.2 亿股」「中报业绩预减」
    summary: str = ""             # 一句话说明
    risk_level: str = "info"      # info | warning | danger（前端直接映射色调）
    risk_score: Optional[float] = None  # 该事件自身的 0–100 风险分
    days_until: Optional[int] = None    # 距事件日的自然日数（已过期/历史事件为 None 或负）
    source: str = "unavailable"   # eastmoney | tushare
    details: Dict[str, Any] = Field(default_factory=dict)  # 事件特有字段（free_ratio / forecast_type / net_buy / change_ratio…）

class EventSnapshot(BaseModel):
    """Event calendar + composite risk for one symbol."""
    as_of: Optional[date] = None
    items: List[EventItem] = Field(default_factory=list)   # 按 event_date 升序
    event_risk_score: Optional[float] = None  # 0–100 综合事件风险
    high_risk_count: int = 0                  # risk_level == "danger" 的事件数
    source: str = "unavailable"
    error: Optional[str] = None
```

在 `SymbolSnapshot`（`agent/src/stock_tracker/models.py`）增加一个字段：

```python
events: Optional[EventSnapshot] = None
```

### 2.2 新模块 `agent/src/stock_tracker/events_data.py`

完全对齐 `valuation_data.py` 的「纯函数 + TTL 缓存 + per-symbol 隔离 + 永不抛异常」范式：

```python
# 常量
_DEFAULT_HORIZON_DAYS = 90        # 未来事件窗口（对齐 lockup 工具默认值）
_DRAGON_TIGER_LOOKBACK_DAYS = 5   # 龙虎榜只看近 5 个交易日
_CACHE_TTL_SECONDS = 30 * 60
_REQUEST_DELAY_SECONDS = 0.15

class EventsDataCache: ...        # 与 ValuationDataCache 同构，key = (code, trading_date)

# —— 纯函数（可单测）——
def parse_lockup_events(records, horizon_days) -> List[EventItem]: ...
def parse_forecast_events(rows) -> List[EventItem]: ...
def parse_holder_trade_events(rows) -> List[EventItem]: ...
def dragon_tiger_events(appearances) -> List[EventItem]: ...

def _risk_level(score: Optional[float]) -> str:      # ≥70 danger / ≥40 warning / else info
def compute_event_risk_score(items) -> Optional[float]:  # 综合评分（见 §3）
def build_event_snapshot(items, source, error=None) -> EventSnapshot:

# —— 编排（网络，永不抛异常）——
def _fetch_one_lockup(code, horizon_days) -> List[EventItem]: ...   # 调公开函数
def _fetch_one_forecast(code) -> List[EventItem]: ...               # tushare best-effort
def _fetch_one_holder_trade(code) -> List[EventItem]: ...           # tushare best-effort
def _fetch_dragon_tiger_board(days) -> Dict[str, List[EventItem]]:  # 全市场一次，按 code 分组

def load_events_data(codes, *, end_date=None, cache=None, horizon_days=90) -> Dict[str, EventSnapshot]:
```

**两个关键设计取舍**（需评审确认）：

1. **龙虎榜走「全市场一次查询 + 按 code 过滤」**，而不是 per-symbol 查询。因为 `dragon_tiger_tool` 必须传 `date`，无法反向知道某股哪天上榜；全市场榜单一次拉近 5 个交易日、再 `SECURITY_CODE` 过滤 watchlist，请求数从 O(N×5) 降到 O(1)，且天然命中缓存。
2. **解禁/业绩预告/增减持走 per-symbol**，与 `valuation_data.py` 一致（逐个 code 请求 + `_REQUEST_DELAY_SECONDS` 间隔 + 失败隔离）。

### 2.3 复用工具的最小改造（只加公开函数，不动原逻辑）

- `agent/src/tools/lockup_expiry_tool.py`：新增
  ```python
  def fetch_lockup_records(code: str, horizon_days: int = 90) -> list[dict]:
      """个股未来 horizon 天解禁记录（复用 _fetch_lockups + _shape_record）。"""
  ```
  （`get_lockup_expiry` 内部也改为调用它，保持单一实现。）

- `agent/src/tools/dragon_tiger_tool.py`：新增
  ```python
  def fetch_recent_board(days: int = 5) -> list[dict]:
      """近 days 个交易日全市场龙虎榜 appearance 记录。"""
  ```

- `agent/src/tools/tushare_fallbacks.py`：新增 `fetch_forecast(code)`、`fetch_holder_trade(code)`，字段口径参照 tushare 文档（`forecast.type` / `p_change_min` / `p_change_max`；`stk_holdertrade.in_de` / `change_ratio` / `holder_type`），缺 token 抛 `TushareFallbackUnavailable`。

### 2.4 Engine 集成（`agent/src/stock_tracker/engine.py`）

1. `__init__` 加 `self._events_cache = EventsDataCache()`。
2. `_seed_caches_from_previous`：同一交易日时，把 `symbol.events` 塞回缓存（与 capital/valuation 同款「同日不重复请求被节流的东财」逻辑）。
3. `refresh()` 在 valuation 之后加：
   ```python
   events_data: Dict[str, EventSnapshot] = {}
   try:
       events_data = load_events_data(self.config.watchlist, end_date=trading_date, cache=self._events_cache)
   except Exception:
       logger.exception("Event data fetch failed")
   ```
4. `_analyze_symbol(...)` 增加 `events: Optional[EventSnapshot] = None` 入参，构造 `SymbolSnapshot` 时传入 `events=events`。

### 2.5 分析器注入（`agent/src/stock_tracker/analyzer.py`）

- `_serialize_symbol` 增加 `"events": _dump(symbol.events)`。
- `_ANALYSIS_DIRECTIVE` 增补一条维度：
  ```
  - 事件日历：未来 90 天解禁、业绩预告、龙虎榜、股东增减持，及综合事件风险分。
  ```

---

## 3. 事件风险评分规则（可解释、可配置）

每个事件先算子分（0–100），综合分 = **主导风险 max(sub_scores) + 每多一个高危事件 +5，封顶 100**；无任何事件则 `None`。规则与 `valuation_data.compute_quality_score` 的「可解释 + 集中一处 + 可调权」一致。

| 事件类型 | 子分规则（举例） |
|---|---|
| 解禁 | `free_ratio` ≥5% 且 ≤30 天 → 80+；1–5% 或 30–90 天 → 40–60；其余 → info |
| 业绩预告 | type ∈ {预减/首亏/续亏} → 75+；{略减/略增/扭亏} → 40；{预增/续盈} → 低危（正向不计风险） |
| 股东增减持 | `DE` 且 `change_ratio` ≥1% → 80；`DE` <1% → 45；`IN` → 正向 |
| 龙虎榜 | 净卖出 `net_buy<0` 且金额大 → 55；净买入 → 正向 |

`risk_level` 由 `event_risk_score` 映射：**≥70 danger / ≥40 warning / 其余 info**，前端直接复用现有的 `text-danger / text-warning / text-info` 色调。

---

## 4. 前端设计

1. **类型**（`frontend/src/lib/api.ts`）：新增 `EventItem`、`EventSnapshot`，并在 `SymbolSnapshot` 加 `events?: EventSnapshot | null`。
2. **助手**（`frontend/src/lib/stockTracker.ts`）：新增 `getEventRiskToneClass(level)`、`formatEventDate(date)`、`formatEventRiskScore(score)`。
3. **新组件** `frontend/src/components/stock-tracker/EventTimelineCard.tsx`：
   - 参照 `ValuationCard.tsx` 的卡片结构（`ChartCardHeader` + 空态 + 列表）。
   - 时间线按 `event_date` 升序渲染未来 90 天事件；`risk_level=danger` 的事件标红高亮，`warning` 标黄。
   - 无数据 / 无事件时显示空态（`noEventData`）。
4. **接线**（`frontend/src/pages/StockTracker.tsx`）：
   - 在 `DETAIL_CARD_COMPONENTS` 末尾追加 `EventTimelineCard`。
   - 默认 `detail_card_count` 由 `5` → `6`，保证新卡默认可见。
5. **i18n**：补齐 `stockTracker.*` 文案键（`eventTitle` / `eventExplanation` / `noEventData` / `eventLockup` / `eventForecast` / `eventDragonTiger` / `eventHolderTrade` / `eventRiskScore` 等）。

---

## 5. 测试（`agent/tests/stock_tracker/`）

新增 `test_events_data.py`（纯函数单测，marker `unit`，无网络）：
- `parse_lockup_events`：字段映射 + 未来 90 天窗口过滤 + 高危标定；
- `parse_forecast_events` / `parse_holder_trade_events` / `dragon_tiger_events`：字段与方向；
- `compute_event_risk_score`：边界（空、单事件、多高危、封顶 100）、`_risk_level` 阈值映射；
- `load_events_data` 的失败隔离（mock 网络抛异常 → 返回带 error 的 `EventSnapshot`，不抛出）。

扩展：
- `test_engine.py`：`_analyze_symbol` 正确挂载 `events`；
- `test_analyzer.py`：`_serialize_symbol` 输出含 `events` 且 prompt 含事件风险。

---

## 6. 验收标准（对齐改进计划原文）

- [ ] 每个 symbol 的快照含 `events`，未来 90 天关键事件按日期展示，**大额解禁 / 业绩预减 / 大股东减持标红**。
- [ ] 解禁 + 龙虎榜数据源稳定（复用现有东财工具，零新网络面）；业绩预告 / 增减持走 Tushare 兜底，缺失时 `error` 隔离、不阻塞刷新。
- [ ] 综合 `event_risk_score` 计算规则可解释，有单测覆盖评分边界。
- [ ] 事件风险注入 LLM 分析 prompt，报告能引用事件风险。
- [ ] 新增/改动文件无语法、编译错误；抽离可复用逻辑（解禁/龙虎榜公开函数单点实现）；`load_events_data` 采用 TTL 缓存避免重复请求。

---

## 7. 实施步骤（分两阶段）

### Phase A（P0，核心，解禁 + 龙虎榜）

1. `models.py` 加 `EventItem` / `EventSnapshot` / `SymbolSnapshot.events`；
2. `lockup_expiry_tool.py` / `dragon_tiger_tool.py` 暴露公开纯函数；
3. `events_data.py` 实现解禁 + 龙虎榜两条链路 + 缓存 + `compute_event_risk_score`；
4. `engine.py` 集成 + `analyzer.py` 注入；
5. 前端类型 + `EventTimelineCard` + 接线 + i18n；
6. `test_events_data.py` 单测 + engine/analyzer 扩展。

### Phase B（P1，可选增强，业绩预告 + 股东增减持）

1. `tushare_fallbacks.py` 加 `fetch_forecast` / `fetch_holder_trade`；
2. `events_data.py` 接两条 best-effort 链路（token 缺失时记 error）；
3. 前端时间线补两类事件标签 + 补单测。

---

## 8. 文档同步（按项目约定）

落地后需同步：
- [stock-tracker-improvement-plan.md](stock-tracker-improvement-plan.md) 跟踪表：2.9 → 已完成 + 备注；
- `docs/PROJECT_INDEX.md`：`stock_tracker` 模块描述补「事件日历 `events_data.py`」；
- 相关文件清单补 `events_data.py` 与 `EventTimelineCard.tsx`。

---

## 9. 待确认事项

1. **业绩预告 / 股东增减持**依赖 Tushare 2000 积分，token 是否已具备该权限？（没有也不影响，Phase B 会自动降级）
2. 默认 `detail_card_count` 从 5 提到 6，让事件卡默认可见——是否接受？还是保持 5、让用户手动在设置里开？
