# stock-tracker 改进计划

> 本文档用于跟踪 A 股多周期股票追踪器（`stock_tracker`）的后续优化方向。
> 创建时间：2026-09-01
> 最后更新：2026-09-03（已落地 2.3 风险指标、2.5 行业强度看板、2.6 估值与质量、2.9 事件日历、2.10 AI 分析结构化升级、2.15 题材热度、2.16 市场情绪、2.17 一致预期、2.18 筹码集中度；2.19 财报速读进行中）

## 一、现状概述

`stock_tracker` 当前是一个基于日频 OHLCV 的多周期技术面监控看板，覆盖后端计算引擎、前端展示面板与 LLM 分析报告。

**已具备能力**：
- 多标的、多周期（默认 10/20/60 日）技术面跟踪
- 5 个可插拔信号检测器：放量、突破、均线排列、RSI 超买超卖、融资余额扩张
- 实时报价轮询与涨跌幅展示
- 融资融券历史图表
- 基于快照的 LLM 量化分析报告
- 配置、快照、分析报告的本地持久化

**核心短板（2026-09-02 更新）**：资金面 / 基本面 / 风险 / 横向比较已补齐；当前缺口是**题材热度与市场情绪面**（「炒作预期」的直接构成）以及**一致预期与筹码集中度**（中长线择时的领先指标，低频口径与日频维度不同）。另多周期共振（2.4）与信号绩效验证（2.7）仍未落地。

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

### P1.5 —— 题材、情绪、预期与筹码（炒作预期 + 中长线择时）

> 前置背景：P0–P1 已补齐技术面 / 资金面 / 基本面 / 事件面，但「炒作预期」与「中长线择时」所需的两类信息仍缺——题材热度与市场情绪（高频），以及一致预期与筹码集中度（低频，数据口径与缓存策略与日频维度不同）。本节补齐，编号延续 2.15–2.18。

#### 2.15 题材 / 概念热度
- **目标**：识别个股所属概念板块、概念热度排名与概念内涨停家数，回答「这只票挂没挂进当前主线、离风口有多远」。
- **投资人价值**：A 股炒作按「概念」而非「行业」展开；中长线需分辨「有产业逻辑的题材」与「纯情绪脉冲」，避免买入蹭概念的边缘股。
- **涉及模块**：`src/tools/sector_tool.py`（新增 `resolve_concept_boards` / `fetch_concept_board_ranking`）、新增 `src/stock_tracker/concept_data.py`、`models.py`、`engine.py`、前端 `ConceptHeatCard.tsx`、表格「概念」列、`SectorStrengthBoard.tsx` 加「行业|概念」tab。
- **大致方案**：
  1. `resolve_concept_boards(code)`：复用 `slist` `spt=3`（已返回行业+概念混合列表），拆出概念类行返回概念板块名列表。
  2. `fetch_concept_board_ranking(limit)`：复用 `clist`，`fs=m:90+t:3`（概念板块口径），返回涨幅 / 主力净流入 / 涨跌家数 / 龙头。
  3. 新增 `ConceptSnapshot`（挂 `SymbolSnapshot.concept`）与 `ConceptBoardStrength`（挂 `TrackerSnapshot.concepts`），含 `hottest_concept`、`hottest_concept_rank`、`concept_heat_score`、`limit_up_count`。
  4. 热度评分：`0.40×概念涨幅分位 + 0.30×主力净流入分位 + 0.30×概念内涨停家数分位`（缺维重归一化，仿 `_PROSPERITY_WEIGHTS`）；涨停家数复用 2.16 全市场涨停池聚合，零额外请求。
  5. 前端：概念 chips（最热着色）+ 最热概念排名 + 热度分；表格加「概念」列；`SectorStrengthBoard` 加「行业|概念」tab（概念榜表头「景气度」换「涨停家数」）。
- **验收标准**：
  - 每个 symbol 展示所属概念与最热概念排名。
  - 新增「概念热度」榜单，概念榜覆盖东财概念板块。
  - 热度评分规则可解释、有单测。

#### 2.16 市场情绪温度计
- **目标**：产出全市场涨停 / 跌停 / 炸板 / 连板高度 / 涨跌家数等情绪指标，合成 0–100 情绪温度，回答「当前是进场还是退潮时点」。
- **投资人价值**：情绪温度到顶（高炸板率、连板断板）正是中长线应避开「炒作退潮」的时刻，是择时避雷的关键。
- **涉及模块**：新增 `src/stock_tracker/sentiment_data.py`、`models.py`（`MarketSentimentSnapshot`）、`engine.py`、前端 `MarketSentimentBar.tsx`。
- **大致方案**：
  1. 新增 `MarketSentimentSnapshot`（挂 `TrackerSnapshot.market_sentiment`）：`limit_up_count` / `limit_down_count` / `broken_board_count` / `broken_ratio` / `max_board_height` / `board_ladder` / `up_count` / `down_count` / `prev_limit_up_perf` / `sentiment_score`。
  2. 数据源：东财涨停池（`push2ex` getTopicZTPool，半开放）为主源，Tushare `limit_list_d` 与打板专题「涨停连板天梯」兜底（复用 `tushare_fallbacks`，token 缺失自动降级）。
  3. 情绪评分：`0.30×涨停家数分位(相对近20日) + 0.25×(1−炸板率) + 0.25×连板高度分位 + 0.20×昨日涨停溢价分位`，全部相对近 20 日分位。
  4. 前端：全宽情绪温度条（冰点/偏冷/中性/偏热/过热五档，蓝→灰→红渐变）+ 关键分项。
- **验收标准**：
  - 快照含市场情绪字段，涨停/连板数据来源稳定或有降级。
  - 情绪温度分档规则可解释、有单测。
  - 前端展示温度条与关键分项。

#### 2.17 盈利预期 / 一致预期
- **目标**：引入机构一致预期 EPS、目标价、评级分布与盈利预测修正，回答「当前 PE 是真贵还是假贵」。
- **投资人价值**：中长线收益 = 预期差；`forward_pe` 对比历史 PE 分位能区分「估值高但利润将爆发」与「真泡沫」。
- **涉及模块**：新增 `src/stock_tracker/consensus_data.py`、`models.py`（`ConsensusSnapshot`）、`engine.py`、前端 `ConsensusCard.tsx`。
- **大致方案**：
  1. 新增 `ConsensusSnapshot`（挂 `SymbolSnapshot.consensus`）：`analyst_count` / `consensus_eps_cur` / `consensus_eps_next` / `forward_pe` / `target_price_avg` / `upside_pct` / `rating_distribution` / `rating_score` / `eps_revision_pct`。
  2. 数据源：东财研报（datacenter 券商研报，no-auth）为主源，Tushare `report_rc`（需 120+ 积分、试用每天 10 次）兜底。
  3. 派生「预期差」：`forward_pe` vs 历史 PE 分位并排展示；`eps_revision_pct` 上/下修着色。
  4. 前端 `ConsensusCard`：目标价区间条（现价 marker）+ 覆盖机构 + 评级分布 + 预期 PE vs 历史分位。
- **验收标准**：
  - 一致预期字段展示正确，目标价空间计算有单测。
  - 东财失败时 Tushare 兜底、无 token/积分时降级为 `None` 不影响主流程。
  - LLM 分析注入一致预期字段。

#### 2.18 筹码集中度 / 机构动向
- **目标**：引入股东户数变化、户均持股、北向/公募持仓变动，合成筹码集中度评分，回答「谁在买、主力吸够没有」。
- **投资人价值**：股东户数下降（吸筹）是中长线最领先的指标之一，比价格早 1–2 个季度。
- **涉及模块**：`src/tools/shareholder_count_tool.py`（拆出代码级 `fetch_shareholder_count`）、新增 `src/stock_tracker/chip_data.py`、`models.py`（`ChipSnapshot`）、`engine.py`、前端 `ChipCard.tsx`。
- **大致方案**：
  1. 新增 `ChipSnapshot`（挂 `SymbolSnapshot.chip`）：`holder_count` / `holder_count_change_pct` / `holder_trend` / `avg_hold_amount` / `northbound_holding_ratio` / `fund_holding_ratio` / `chip_concentration_score`。
  2. 数据源：股东户数复用现有 `shareholder_count_tool`（东财 `RPT_HOLDERNUMLATEST`，拆出代码级函数）；北向个股持股 Tushare `hk_hold`（历史/季度口径）；公募持仓 Tushare `fund_portfolio`（季度，滞后 15–45 天，仅辅助）。
  3. 评分：`0.40×股东户数下降分位 + 0.30×户均持股上升分位 + 0.30×(北向/公募增持分位)`；`holder_trend` 连续 2 期下降判「吸筹」。
  4. 低频缓存：股东户数/基金持仓季度级，新增 `ChipDataCache`（TTL 7 天）与 `ConsensusDataCache`（TTL 1 天），与现有日频 `ValuationDataCache` 分离。
  5. 前端 `ChipCard`：股东户数 mini 折线（下降绿/上升红）+ 北向/公募持仓 + 集中度进度条。
- **验收标准**：
  - 股东户数/北向/公募数据展示正确，环比符号清晰。
  - 集中度评分规则可解释、有单测。
  - 低频缓存命中时跳过网络，避免撞东财/Tushare 节流与积分墙。

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
5. **第五阶段（4–6 周，炒作预期 + 中长线择时）**：先 2.18 筹码集中度（股东户数数据现成，成本最低）与 2.15 概念热度（纯东财、零 token、最稳）；再 2.16 市场情绪温度计（依赖涨停数据源稳定性）；最后 2.17 一致预期（受 `report_rc` 积分门槛，先以东财研报为主源跑通）。

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
| 2.9 | 事件与日历集成 | 已完成 | jinchu | 2026-09-02 | 2026-09-02 | 新增 `events_data.py`（解禁/业绩预告/龙虎榜/增减持 → `EventSnapshot` + 综合事件风险分 0–100）挂到 `SymbolSnapshot.events`；`EventTimelineCard.tsx` 事件时间线卡；事件风险注入 LLM 分析。解禁与龙虎榜复用东财工具（`fetch_lockup_records` 取 ALL 列含 `free_ratio` 真实比例；`fetch_recent_board` 按天回扫近 5 交易日）；业绩预告/增减持走 Tushare 兜底（token 缺失自动降级，按 symbol 记 error）。默认 detail_card_count 提到 6，事件卡默认可见。详见 [stock-tracker-2.9-event-calendar.md](stock-tracker-2.9-event-calendar.md) |
| 2.10 | AI 分析结构化升级 | 已完成 | jinchu | 2026-09-02 | 2026-09-02 | LLM 输出结构化 `AnalysisReport`：action 四值(buy/hold/reduce/avoid)+置信度 0–100+买入/目标区间+止损+减仓触发+跟踪指标；注入资金流/风险/行业景气度字段；**focus 概念已移除**（固定全维度，前端保留可选「补充指令」透传 user_prompt）；预测持久化 + 只读 track-record 清单（pending/active/hit_target/stopped_out，用最新价比对，暂不算胜率，见 2.7） |
| 2.11 | 分钟级盘中监控 | 待开始 | - | - | - | |
| 2.12 | 预警通知系统 | 待开始 | - | - | - | |
| 2.13 | 多 watchlist / 组合管理 | 待开始 | - | - | - | |
| 2.14 | 每日复盘报告导出 | 待开始 | - | - | - | |
| 2.15 | 题材/概念热度 | 已完成 | jinchu | 2026-09-03 | 2026-09-03 | 新增 `concept_data.py`（概念榜 `clist fs=m:90+t:3` + watchlist 概念归属，`ConceptStrength` + `ConceptSnapshot`）；热度分 = 0.40×涨幅分位 + 0.30×主力净流入分位 + 0.30×涨停家数分位（缺维重归一化），涨停家数复用 2.16 市场涨停池；前端 `ConceptHeatCard` + `SectorStrengthBoard` 加「行业|概念」tab（概念 tab 以涨停家数替代景气度） |
| 2.16 | 市场情绪温度计 | 已完成 | jinchu | 2026-09-03 | 2026-09-03 | 新增 `sentiment_data.py`（东财 `push2ex getTopicZTPool` 主源 + tushare `limit_list_d`/`limit_step` 兜底，token 缺失静默降级）；温度分 0–100（涨停家数/炸板率/连板高度/昨日涨停溢价 0.30/0.25/0.25/0.20，固定参考刻度近似近 20 日分位）；前端 `MarketSentimentBar` 全宽温度条 |
| 2.17 | 盈利预期/一致预期 | 已完成 | jinchu | 2026-09-03 | 2026-09-03 | 新增 `consensus_data.py`（东财研报主源 + THS 一致预期 + tushare `report_rc` 目标价/EPS 修正兜底）；`ConsensusDataCache` TTL 1 天；forward PE/上行空间由 engine 拿 close 后回填；前端 `ConsensusCard` |
| 2.18 | 筹码集中度/机构动向 | 已完成 | jinchu | 2026-09-03 | 2026-09-03 | 新增 `chip_data.py`（东财股东户数主源 + 北向 `hk_hold`/公募 `fund_portfolio` tushare 兜底）；`ChipDataCache` TTL 7 天；集中度分 0–100（户数下降/户均上升/机构增持 0.40/0.30/0.30）；前端 `ChipCard` |
| 2.19 | 财报速读（Phase 1 数据卡） | 进行中 | jinchu | 2026-09-03 | - | 手动按需单标的「阅读财报」：新 `financial_reports_data.py` + `src.tools.financial_statements_tool.fetch_financial_indicators`（东财 `RPT_F10_FINANCE_MAINFINADATA` 多期倒序）；新模型 `FinancialPeriod`/`FinancialReportSnapshot`（不挂 `SymbolSnapshot`）；GET `/api/stock-tracker/symbols/{code}/financial-report`；红旗 + beat/miss（对比一致预期 EPS）；前端 `FinancialReportCard`（多期指标表，1/4/8 期切换，默认 4）；Phase A（LLM 速读点评）未做 |

---

## 五、相关文件

- 后端核心：`agent/src/stock_tracker/engine.py`、`signals.py`、`capital_data.py`、`valuation_data.py`、`sector_data.py`、`events_data.py`、`_convert.py`、`risk.py`、`models.py`、`analyzer.py`、`track_record.py`、`financial_reports_data.py`（2.19）
- API 路由：`agent/src/api/stock_tracker_routes.py`（含 2.19 `GET /symbols/{code}/financial-report`）
- 前端页面：`frontend/src/pages/StockTracker.tsx`
- 前端组件：`frontend/src/components/stock-tracker/TrackerTable.tsx`、`TrackerCharts.tsx`、`MarginChartCard.tsx`、`FundFlowChartCard.tsx`、`RpsChartCard.tsx`、`RiskMetricsCard.tsx`、`ValuationCard.tsx`、`EventTimelineCard.tsx`、`SectorStrengthBoard.tsx`、`TrackerConfigPanel.tsx`、`TrackerAnalyzePanel.tsx`、`TrackerAnalysisReport.tsx`、`TrackerTrackRecord.tsx`、`FinancialReportCard.tsx`（2.19）
- 前端库：`frontend/src/lib/stockTracker.ts`（含 action/status tone 与 label key 助手）、`frontend/src/lib/api.ts`（分析/预测类型）
- 项目索引：`docs/PROJECT_INDEX.md`
