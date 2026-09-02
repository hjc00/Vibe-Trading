# stock-tracker 改进计划

> 本文档用于跟踪 A 股多周期股票追踪器（`stock_tracker`）的后续优化方向。
> 创建时间：2026-09-01
> 最后更新：2026-09-02（已落地 2.3 风险指标、2.5 行业强度看板、2.6 估值与质量指标、2.10 AI 分析结构化升级）

## 一、现状概述

`stock_tracker` 当前是一个基于日频 OHLCV 的多周期技术面监控看板，覆盖后端计算引擎、前端展示面板与 LLM 分析报告。

**已具备能力**：
- 多标的、多周期（默认 10/20/60 日）技术面跟踪
- 5 个可插拔信号检测器：放量、突破、均线排列、RSI 超买超卖、融资余额扩张
- 实时报价轮询与涨跌幅展示
- 融资融券历史图表
- 基于快照的 LLM 量化分析报告
- 配置、快照、分析报告的本地持久化

**核心短板**：信号维度偏技术面单一，缺少资金面、基本面、风险控制和横向比较能力；多周期分析停留在「同一信号重复计算」，未形成共振/背离判断；信号触发后缺少绩效验证。

---

## 二、改进方向与优先级

### P0 —— 核心指标增强（建议 1–2 周内启动）

#### 2.1 主力资金流向信号
- **目标**：在 signals 体系中新增北向/主力资金净流入相关信号，当前仅融资融券余额过于单薄。
- **投资人价值**：A 股是资金驱动市场，判断「谁在购买、谁在卖出」比单纯看价格更重要。
- **涉及模块**：`agent/src/stock_tracker/signals.py`、`agent/src/stock_tracker/engine.py`、`capital_data.py`、前端表格与详情卡片。
- **大致方案**：
  1. 评估复用 `src.tools` 中已有的资金流/龙虎榜工具，或新增 `capital_flow_tool`。
  2. 新增 `CapitalMetrics` 字段（如主力净流入、大单净流入、散户净流入、5 日累计流向）。
  3. 新增 detector：`net_inflow_spike`（净流入异常放大）、`main_force_inflow`（主力资金连续流入）。
  4. 前端在表格中增加资金流向列，详情卡片展示 5 日流向趋势图。
- **验收标准**：
  - 后端能稳定拉取主力净流入数据，失败时填充 error 不影响主流程。
  - 新增 ≥2 个资金流信号并注册到 detector registry。
  - 前端能展示资金流向列和 mini 趋势图。

#### 2.2 个股相对强弱（RPS）
- **目标**：计算个股相对沪深 300 / 所属行业的超额收益分位，识别真正的强势股。
- **投资人价值**：区分「自身走强」与「随大盘普涨」，避免追涨弱势补涨股。
- **涉及模块**：`agent/src/stock_tracker/engine.py`、`models.py`、前端表格/图表。
- **大致方案**：
  1. 在 `PeriodMetrics` 中新增 `rps_market`（相对 watchlist + 沪深300 的百分位）、`rps_sector`（相对同行业 watchlist 股票的百分位）、`benchmark_return_pct`。
  2. 通过 `fetch_market_data` 拉取沪深300指数（`000300.SH`），失败时回退到沪深300 ETF（`510300.SH`）。
  3. 通过 `src.tools.sector_tool.resolve_industry_board` 解析个股行业板块，失败时留空。
  4. 在 engine 中先完成所有 symbol 的周期指标计算，再统一做横截面 RPS 排名并回填。
  5. 在 `rankings` 中新增 `rps_market_{period}` 和 `rps_sector_{period}` 排行榜。
  6. 前端表格增加 RPS 列、展开行展示 RPS Market/RPS Sector、详情卡展示 RPS 与超额收益，并新增 `RpsChartCard` 趋势图。
- **验收标准**：
  - 每个 symbol 的 period metrics 包含 RPS 字段。
  - 新增「RPS 排名」榜单。
  - 前端展示 RPS 分位和颜色标识。

#### 2.3 风险指标：ATR、最大回撤、Beta
- **目标**：为每个标的补充基础风险度量，支撑止损和仓位决策。
- **投资人价值**：知道买多少、跌到哪该止损，是完整交易闭环的前提。
- **涉及模块**：`agent/src/stock_tracker/engine.py`、`models.py`、新增 `risk.py`、前端新增 `RiskMetricsCard.tsx`。
- **大致方案**：
  1. 在 `SymbolSnapshot` 中新增 `risk: RiskMetrics`（标的级，非周期级），含 `atr_14`、`atr_pct`、`max_drawdown_60d`、`beta_vs_index`、`beta_window`、`benchmark_code`、`stop_loss_price`、`stop_loss_atr_multiple`。
  2. 新增 `risk.py` 提供纯函数：`compute_atr`（Wilder 平滑）、`compute_max_drawdown`、`compute_beta`（OLS 斜率，重叠样本 ≥30 才输出）。
  3. Beta 复用 RPS 的沪深 300 benchmark（`000300.SH` → `510300.SH` 回退），零新增网络请求。
  4. 阈值可配置：`atr_period`、`max_drawdown_window`、`beta_window`、`stop_loss_atr_multiple`。
  5. 前端新增 `RiskMetricsCard` stat 卡，展示 ATR/回撤/Beta 与止损参考价（close − k×ATR）。
- **验收标准**：
  - 后端正确计算 ATR、最大回撤、Beta。
  - 前端展示风险指标和止损参考价。
  - 增加对应单元测试。

#### 2.4 多周期共振评分
- **目标**：把同一 symbol 在不同周期的离散信号聚合成一个综合评分；同时支持更长周期（120/250/500 日），辅助中线趋势级别判断。
- **投资人价值**：从「信号数量」升级到「周期是否共振」，过滤矛盾信号；长周期（60/120/250 日）帮助识别中线趋势方向。
- **涉及模块**：`agent/src/stock_tracker/engine.py`、`models.py`、前端 Summary/表格。
- **大致方案**：
  1. 扩展 `TrackerConfig.periods` 支持 120/250/500 日等长周期。
  2. 定义评分规则：短周期（10D）触发给分、长周期（60D/120D/250D）同向触发加权更高、反向信号扣分。
  3. 在 `SymbolSnapshot` 中新增 `multi_timeframe_score`（0–100）和 `dominant_timeframe`。
  4. 新增趋势级别信号：`market_regime`（牛/熊/震荡），基于价格相对 120/250 日均线位置。
  5. 新增「共振评分」排行榜，前端用进度条或颜色展示分数。
- **验收标准**：
  - 每个 symbol 都有共振评分与趋势级别判断。
  - 评分规则可配置或可解释。
  - 新增测试覆盖评分边界。

---

### P1 —— 横向比较与基本面（建议 1–2 个月内启动）

#### 2.5 行业/板块强度看板
- **目标**：展示每个 symbol 所在行业的平均收益、资金流向、RPS，识别强势行业；从中长线角度，行业景气度是选股的前置条件。
- **涉及模块**：新增 `sector_data.py`、engine、前端新增 SectorSummary 组件。
- **大致方案**：
  1. 维护 A 股行业映射表（或从数据源拉取），将 symbol 映射到一级/二级行业。
  2. 在每次 refresh 时按行业聚合 RPS、资金流入、平均收益、行业 ROE 趋势、毛利率变化。
  3. 新增行业景气度评分（营收增速、ROE 趋势、毛利率、资本开支周期、库存周期综合）。
  4. 前端增加「行业强度」排行榜或热力图，并在 symbol 详情中展示所属行业强度排名。
- **验收标准**：
  - 每个 symbol 展示所属行业及行业强度排名。
  - 行业景气度评分有明确计算规则与测试。
  - 行业强度看板至少覆盖 31 个申万一级行业。

#### 2.6 估值与质量指标
- **目标**：引入估值安全边际与基本面质量评分，避免中长线买入估值高位或盈利质量差的标的。
- **涉及模块**：新增 `valuation_data.py`、engine、models、前端详情。
- **大致方案**：
  1. 接入已有的财务数据工具或新增数据源，拉取 PE_TTM、PB、PS、PCF、ROE、净利润增速、股息率、经营现金流/净利润 等指标。
  2. 在 `SymbolSnapshot` 中新增 `valuation` 字段，含 PE_TTM、PB、PS、ROE、PEG、股息率、3 年/5 年/10 年估值分位。
  3. 新增 `fundamental_quality_score`（0–100），综合 ROE 稳定性、盈利质量、成长性、毛利率稳定性、研发投入强度。
  4. 前端新增 `ValuationCard`，用颜色标识估值分位（<30% 偏低、30–70% 合理、>70% 偏高）。
- **验收标准**：
  - 估值指标展示正确，分位数计算逻辑有测试。
  - 基本面质量评分计算规则可解释、可配置阈值。
  - LLM 分析 prompt 中注入估值与质量数据。

#### 2.7 信号历史绩效追踪
- **目标**：统计信号触发后的未来收益，验证信号是否真正有效。
- **涉及模块**：`agent/src/stock_tracker/store.py`、engine、新增 `signal_performance.py`、前端报告。
- **大致方案**：
  1. 保存每日快照中每个 symbol 的触发信号。
  2. 当新快照生成时，读取历史快照计算 T+1/T+5/T+20 收益。
  3. 按信号/信号组合输出胜率、平均收益、盈亏比。
  4. 在 LLM 分析 prompt 中注入绩效数据，提升分析可信度。
- **验收标准**：
  - 能计算并持久化历史信号绩效。
  - 前端或报告可查看各信号组合的胜率/盈亏比。

---

### P2 —— 风险控制与交易闭环（建议 2–3 个月内启动）

#### 2.8 组合风险检查
- **目标**：针对 watchlist 计算相关性矩阵、行业集中度、波动率目标仓位。
- **涉及模块**：engine、新增 `risk_report.py`、前端 RiskPanel。
- **大致方案**：
  1. 计算 watchlist 内 pairwise 收益相关性。
  2. 识别高相关性组合与行业集中风险。
  3. 基于波动率目标法给出仓位建议。
- **验收标准**：新增「风险检查」视图，展示相关性热力图和风险提示。

#### 2.9 事件与日历集成
- **目标**：整合财报公告、业绩预告、解禁、龙虎榜、股东增减持等事件，从中长线视角提前识别持股风险。
- **涉及模块**：新增 `events_data.py`、engine、前端 EventTimeline。
- **大致方案**：
  1. 优先接入财报日历与解禁预警。
  2. 在 symbol 详情中展示未来 90 天关键事件，对大额解禁、业绩预减、重要股东减持等高风险事件标红。
  3. 将事件风险纳入 LLM 分析 prompt。
- **验收标准**：事件数据展示正确，数据来源稳定。

#### 2.10 AI 分析结构化升级
- **目标**：让 LLM 输出更聚焦、可执行、可验证的投资建议，强化中长线分析能力。
- **涉及模块**：`agent/src/stock_tracker/analyzer.py`、前端 `TrackerAnalysisReport.tsx`。
- **大致方案**：
  1. 给 LLM prompt 注入行业景气度、估值分位、基本面质量评分、RPS、资金流、风险指标。
  2. ~~新增 focus 模式…~~（已取消：经评审移除 focus 概念，报告固定全维度输出；面板仅保留可选「补充指令」透传 `user_prompt`）。
  3. 要求输出结构化 action：`BUY`/`HOLD`/`REDUCE` + 合理买入区间/目标价区间 + 止损/减仓触发条件 + 关键跟踪指标 + 时间周期 + 置信度。
  4. 保存预测，后续可验证并生成 track record。
- **验收标准**：报告包含明确的 action 和关键价位，预测可后续验证。

> 落地注记（2026-09-02）：action 定为 `buy/hold/reduce/avoid` 四值（另含 `avoid`）；置信度 0–100；注入字段新增资金流/风险/行业景气度；**focus（分析重点）概念已移除**——固定全维度分析，前端仅保留可选「补充指令」输入框，后端仍接受可选 `user_prompt`；track record 本轮做「持久化 + 只读清单」（以最新价分类 pending/active/hit_target/stopped_out），胜率/盈亏比留给 2.7 统一统计。

---

### P3 —— 基础设施与体验（长期）

#### 2.11 分钟级盘中监控
- **目标**：支持分钟级数据刷新，捕捉盘中异动。
- **涉及模块**：`fetch_market_data` 支持分钟 interval、quotes 轮询优化、前端异常检测。

#### 2.12 预警通知系统
- **目标**：当 symbol 触发新信号、触及止损或出现异常波动时推送通知。
- **涉及模块**：新增 alert 模块、WebSocket/SSE 或轮询、用户通知中心。

#### 2.13 多 watchlist / 组合管理
- **目标**：支持同时维护多个 watchlist（如核心持仓、观察池、行业轮动池）。
- **涉及模块**：`TrackerConfig` 改造、store 改造、前端组合切换 UI。

#### 2.14 每日复盘报告导出
- **目标**：生成可导出的每日/每周 stock-tracker 复盘 PDF/Markdown 报告。
- **涉及模块**：新增 report 生成器、前端导出按钮。

---

## 三、推荐实施顺序

1. **第一阶段（2 周）**：落地 2.1 资金流信号 + 2.2 RPS + 2.3 风险指标。
2. **第二阶段（3–4 周）**：落地 2.4 多周期共振评分 + 2.5 行业强度 + 2.6 估值指标。
3. **第三阶段（4–6 周）**：落地 2.7 信号绩效追踪 + 2.8 组合风险检查 + 2.9 事件日历 + 2.10 AI 分析升级。
4. **第四阶段（持续）**：按需推进 P3 基础设施项。

---

## 四、跟踪记录

| 序号 | 改进项 | 状态 | 负责人 | 开始时间 | 完成时间 | 备注 |
|-----|-------|------|-------|---------|---------|------|
| 2.1 | 主力资金流向信号 | 已完成 | jinchu | 2026-09-01 | 2026-09-01 | 东财 daykline 接口在当前网络下受限，已预留 tushare fallback；见 commit `29663f55` |
| 2.2 | 个股相对强弱（RPS） | 已完成 | jinchu | 2026-09-01 | 2026-09-01 | watchlist 内横截面排名 + 沪深300；行业 RPS 依赖 `sector_tool`；见当前提交 |
| 2.3 | 风险指标（ATR/回撤/Beta） | 已完成 | jinchu | 2026-09-01 | 2026-09-01 | 新增 `risk.py` + `RiskMetricsCard`；Beta 复用 RPS benchmark |
| 2.4 | 多周期共振评分 | 待开始 | - | - | - | |
| 2.5 | 行业/板块强度看板 | 已完成 | jinchu | 2026-09-02 | 2026-09-02 | 东财行业板块口径（约 86 个，非申万）；新增 `sector_data.py` + `SectorStrengthBoard`；全市场板块涨跌/资金流排行 + watchlist 聚合 + 简版景气度评分（营收增速/ROE/毛利率 40/40/20）；卡片可折叠（localStorage 记忆），周期趋势列对比各配置周期平均收益 |
| 2.6 | 估值与质量指标 | 已完成 | jinchu | 2026-09-02 | 2026-09-02 | 东财 datacenter `RPT_VALUEANALYSIS_DET`（PE_TTM/PB/PS/PCF/PEG/总市值+3/5/10年分位）+ `RPT_F10_FINANCE_MAINFINADATA`（ROE/毛利率/增速/现金流质量/杠杆→质量评分 0–100）；股息率当前无稳定来源留空，研发投入强度未纳入评分（见 2.6 方案备注） |
| 2.7 | 信号历史绩效追踪 | 待开始 | - | - | - | |
| 2.8 | 组合风险检查 | 待开始 | - | - | - | |
| 2.9 | 事件与日历集成 | 待开始 | - | - | - | |
| 2.10 | AI 分析结构化升级 | 已完成 | jinchu | 2026-09-02 | 2026-09-02 | LLM 输出结构化 `AnalysisReport`：action 四值(buy/hold/reduce/avoid)+置信度 0–100+买入/目标区间+止损+减仓触发+跟踪指标；注入资金流/风险/行业景气度字段；**focus 概念已移除**（固定全维度，前端保留可选「补充指令」透传 user_prompt）；预测持久化 + 只读 track-record 清单（pending/active/hit_target/stopped_out，用最新价比对，暂不算胜率，见 2.7） |
| 2.11 | 分钟级盘中监控 | 待开始 | - | - | - | |
| 2.12 | 预警通知系统 | 待开始 | - | - | - | |
| 2.13 | 多 watchlist / 组合管理 | 待开始 | - | - | - | |
| 2.14 | 每日复盘报告导出 | 待开始 | - | - | - | |

---

## 五、相关文件

- 后端核心：`agent/src/stock_tracker/engine.py`、`signals.py`、`capital_data.py`、`valuation_data.py`、`sector_data.py`、`_convert.py`、`risk.py`、`models.py`、`analyzer.py`、`track_record.py`
- API 路由：`agent/src/api/stock_tracker_routes.py`
- 前端页面：`frontend/src/pages/StockTracker.tsx`
- 前端组件：`frontend/src/components/stock-tracker/TrackerTable.tsx`、`TrackerCharts.tsx`、`MarginChartCard.tsx`、`FundFlowChartCard.tsx`、`RpsChartCard.tsx`、`RiskMetricsCard.tsx`、`ValuationCard.tsx`、`SectorStrengthBoard.tsx`、`TrackerConfigPanel.tsx`、`TrackerAnalyzePanel.tsx`、`TrackerAnalysisReport.tsx`、`TrackerTrackRecord.tsx`
- 前端库：`frontend/src/lib/stockTracker.ts`（含 action/status tone 与 label key 助手）、`frontend/src/lib/api.ts`（分析/预测类型）
- 项目索引：`docs/PROJECT_INDEX.md`
