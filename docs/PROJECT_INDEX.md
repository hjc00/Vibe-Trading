# Vibe-Trading 模块索引

> 深度逐模块索引，按需读取（由根 [CLAUDE.md](../CLAUDE.md) 指向，不随会话自动加载）。
> 代码改动后请同步更新本文件对应模块（见 CLAUDE.md「文档同步约定」）。
> 最后更新：2026-09-01

## 一、项目总览

- `vibe-trading-ai` v0.1.14 —— 自然语言驱动的金融研究 AI Agent（带回测）。
- 上游 `HKUDS/Vibe-Trading`，个人 fork 维护，只推 origin。
- 后端 Python 3.11+（FastAPI + LangChain/LangGraph + DuckDB）；前端 React 19 + TS + Vite + Tailwind；桌面 Electron 壳。
- 构建配置在**仓库根** `pyproject.toml`（`package-dir="agent"`），不在 `agent/` 内。
- 权威结构文档：`README.md` 的 `📁 Project Structure` 章节（约 1777 行）；本文件为精简补充，冲突时以 README 为准。

## 二、目录树

```
Vibe-Trading/
├── agent/                        # Python 后端（主代码库）
│   ├── api_server.py             # FastAPI 装配入口
│   ├── mcp_server.py             # MCP 只读工具服务
│   ├── cli/                      # CLI（REPL + serve/run/mcp 等子命令）
│   ├── src/                      # 主包（见「三」）
│   ├── tests/                    # pytest（见「八」）
│   ├── backtest/                 # 回测包
│   └── SKILL.md                  # 包级清单
├── frontend/                     # Vite + React 前端（见「六」）
├── desktop/electron/             # Electron 桌面壳（见「七」）
├── data/                         # 运行时产物（含 stock_tracker/）
├── artifacts/ bear_advocate/ factor_out/ factor_output/
│                                 # 生成 / scratch 目录
├── assets/                       # 仓库媒体
├── wiki/                         # 独立文档站源码
├── scripts/dev                   # 前后端编排脚本
├── start-web.bat                 # Windows 一键启动
├── docker-compose.yml Dockerfile # 容器化
├── pyproject.toml                # 构建/测试/lint 配置
├── CLAUDE.md docs/               # AI 上下文索引（本文件 + 精简地图）
│   └── plans/                    # 功能改进计划文档
```

## 三、后端 agent/src/ 逐模块

> 路径均相对 `agent/`。括号内为关键文件或要点。

### 3.1 核心运行时

- **`agent/`** — ReAct AgentLoop、工具注册、上下文/工作区/技能加载；`src/agent/skills.py`（技能加载器）、`src/agent/frontmatter.py`（SKILL.md 解析）。
- **`core/`** — Runner + RunStateStore（运行生命周期）。
- **`session/`** — 会话管理、持久化、SSE 流式输出。
- **`providers/`** — LLM 抽象层，~25 家供应商（OpenRouter/OpenAI/Anthropic/DeepSeek/Copilot 等）。
- **`tools/`** — 工具自动发现注册表。
- **`memory/`** — 跨会话记忆（持久化、层级、压缩、语义链接、搜索索引）。
- **`governance/`** — 运行清单 + 哈希链防篡改账本（`manifest.py`、`ledger.py`）。
- **`security/`** — 输出净化。
- **`config/`** — 配置加载 / env schema（`env_schema.py` 定义全部 env 变量）。
- **`preflight.py`** — 启动连通性检查。
- **`goal/` `hypotheses/`** — 研究目标 / 假设运行时（claims/criteria/evidence/audit；持久假设注册表）。
- **`swarm/`** — 多智能体 DAG 执行引擎，30 个预设团队 YAML。

### 3.2 数据与数学

- **`market_data.py`** — 共享行情数据助手（`fetch_market_data` 等）。
- **`openbb_bridge/`** — OpenBB Workspace 自定义 agent 桥接（协议模型 + SSE）。
- **`quantlib/`** — 金融数学原语（时序：ADF/协整/Granger/VIF/GARCH，lazy-import `statsmodels`/`arch`）。
- **`entities/`** — 非日频持仓现金流 spine。
- **`factors/`** — Alpha Zoo（见「四·2」）。

### 3.3 组合与交易

- **`portfolio/`** — 只读多券商聚合。
- **`trading/`** — 连接器 profiles / 操作。
- **`live/`** — 有界自主实盘。
- **`shadow_account/`** — 提取盈利模式 → 可复跑 shadow → 回测 → 渲染（Jinja2 模板）。

### 3.4 扩展与技能

- **`skills/`** — 90 个打包技能（见「四·1」）。
- **`strategy_discovery/`** — Alpha Zoo + SDM 的实证门控 facade（统一策略目录 + 分 regime 证据）。
- **`strategy_store/`** — Strategy Development Manager：产物存储 + 衰减监控。
- **`scheduled_research/`** — 定时研究任务（见「四·3」）。
- **`ui_services.py`** — 前端 run 分析整形。
- **`stock_tracker/`** — A股多周期追踪（见「五」）。

### 3.5 通道与集成

- **`channels/`** — 多 IM 插件适配（Telegram/Discord/Slack/微信/QQ/钉钉/飞书/Matrix 等）。
- **`channelsui/`** — WebUI 兼容助手。
- **`api/`** — HTTP 路由包（见下）。
- **`utils/`** — 仅 `media_decode.py`。

### 3.6 api/ 路由清单

- 业务路由：`sessions_routes.py`、`runs_routes.py`、`portfolio_routes.py`、`swarm_routes.py`、`live_routes.py`、`options_routes.py`、`alpha_routes.py`、`scheduled_routes.py`、`qveris_routes.py`、`attribution_routes.py`、`channels_routes.py`、`connection_routes.py`、`settings_routes.py`、`system_routes.py`、`uploads_routes.py`、`auth_routes.py`、`stock_tracker_routes.py`。
- 基础设施：`models.py`、`security.py`、`helpers.py`、`state.py`、`spa.py`、`_compat.py`、`attribution_core.py`。

## 四、扩展系统详解

### 4.1 skills 技能系统

- 一个「技能」= 每主题场景指南（Markdown + 可选 `references/`、`scripts/`），位于 `agent/src/skills/<name>/SKILL.md`，共 90 个、9 大类。
- frontmatter 仅 `name`/`category`/`description`（偶有 `markets` 等），**`description` 兼作触发词**，无独立 trigger 字段。
- 加载器 `src/agent/skills.py`（+ `src/agent/frontmatter.py`）扫描 `agent/src/skills/` 与用户目录 `~/.vibe-trading/skills/user/`（用户覆盖内置）。
- 两级渐进加载：`get_descriptions()` 把一行 name+description 按 category 注入系统提示；`get_content()` 经 `load_skill` 工具按需加载全文，`split_sections` 支持按章节返回。
- 类别（9 个，按数量）：`analysis`(23)、`strategy`(19)、`tool`(10)、`data-source`(10)、`asset-class`(9)、`flow`(8)、`crypto`(7)、`research`(3)、`risk-analysis`(1)。展示顺序见 `skills.py` 的 `_CATEGORY_ORDER`（`data-source, strategy, analysis, asset-class, crypto, flow, tool`），未列出的 `research`/`risk-analysis` 按字母序排末尾。
- 代表性技能：数据源 `eastmoney/akshare/tushare/mootdx/ccxt/yfinance/qveris`；策略 `chanlun/smc/ichimoku/harmonic/elliott-wave/ml-strategy/pair-trading/sector-rotation/event-driven`；分析 `factor-research/multi-factor/alpha-zoo/quant-statistics/behavioral-finance/macro-analysis/sentiment-analysis/volatility`；资产类别 `crypto-derivatives/defi-yield/onchain-analysis/convertible-bond/options-advanced/etf-analysis`；工作流 `research-goal/thesis-tracker/strategy-generate/strategy-dev-manager/report-generate/trade-journal/shadow-account/backtest-diagnose`。

### 4.2 factors Alpha Zoo

- `agent/src/factors/`：462 个预建 alpha，5 个 Python 家族 `zoo/`：`alpha101`(101)、`gtja191`(191)、`qlib158`(154)、`academic`(12)、`fundamental`(4)。
- 每个因子是 Python 模块 `zoo/<family>/<id>.py`，含 `__alpha_meta__` dict（冻结 pydantic `AlphaMeta` schema：theme/universe/columns_required/decay_horizon/min_warmup_bars）。
- `base.py` = 19 个算子；`registry.py` AST 扫描元数据（不 import）、按需 lazy 计算、输出 sanity gate（拒绝 ±inf / >95% NaN）。
- 消费方：`bench_runner.py`（IC + alive/reversed/dead）、`factor_analysis` 工具、`ZooSignalEngine`。

### 4.3 scheduled_research 定时研究

- 定时任务模型/存储 + 调度器（cron/interval）。
- `playbooks/*.md`（5 个内置）：可运行 prompt，YAML frontmatter（`name/description/markets/suggested_schedule`[5 段 cron]/`suggested_timezone/data_capabilities/variables`），body 作为 `job.prompt` 原样使用。
- `models.py` 校验 schedule（裸毫秒 interval 或简化 5 段 cron，时区感知）；`executor.py` 调度；`playbooks.py` 发现/解析（env `VIBE_TRADING_PLAYBOOK_DIR` 覆盖内置）。

### 4.4 其他高层子系统（各一句）

- `swarm/` — 多智能体 DAG 执行引擎 + 30 预设 YAML 团队。
- `governance/` — 运行清单 + 防篡改哈希链账本。
- `goal/` — 金融研究目标运行时（claims/criteria/evidence/audit）。
- `hypotheses/` — 持久研究假设注册表。
- `strategy_discovery/` — Alpha Zoo + SDM 的实证门控 facade。
- `strategy_store/` — Strategy Development Manager：产物存储 + 衰减。
- `shadow_account/` — 提取盈利模式 → shadow → 回测 → 渲染。
- `memory/` — 持久跨会话记忆（层级、压缩、语义链接、搜索索引）。

## 五、stock_tracker 专项（当前活跃功能）

> A股多周期股票追踪。代码 `agent/src/stock_tracker/`，路由 `agent/src/api/stock_tracker_routes.py`。

| 文件 | 职责 |
|------|------|
| `models.py` | 配置（`TrackerConfig`/`TrackerThresholds`，阈值支持动态字段；`refresh_interval_seconds` 控制实时行情轮询间隔，`detail_card_count` 控制中间详情卡片数量）+ 快照类型 + 代码归一；`PeriodMetrics` 已含 RPS 分位（`rps_market`/`rps_sector`/`benchmark_return_pct`），`SymbolSnapshot` 已含行业板块（`sector_board`）、风险度量（`risk: RiskMetrics`）、估值质量（`valuation: ValuationSnapshot`）、事件日历（`events: EventSnapshot`，内含 `EventItem`）与行业强度排名（`sector_strength_rank`）；`TrackerSnapshot` 含行业强度看板（`sectors: List[SectorStrength]`，内含 `SectorPeriodMetric` 各周期趋势）；资金相关模型 `CapitalMetrics`（含 `FundFlowSnapshot`/`MarginSnapshot` 双维度）/`FundFlowHistoryItem`/`MarginHistoryItem`；分析相关模型（2.10）`AnalysisAction`/`PriceZone`/`SymbolRecommendation`/`PortfolioInsight`/`AnalysisReport`/`TrackRecordItem`；P1.5 新增 `ConceptSnapshot`/`ConceptStrength`（题材热度）、`MarketSentimentSnapshot`（市场情绪）、`ConsensusSnapshot`（一致预期）、`ChipSnapshot`+`ChipHolderItem`（筹码集中度），`SymbolSnapshot` 增挂 `concept`/`consensus`/`chip`，`TrackerSnapshot` 增挂 `market_sentiment`/`concepts` |
| `signals.py` | 信号注册表 + 自描述 `SignalMeta`；内置放量/突破/均线排列/RSI，`margin_expansion` 与 `net_inflow_spike`/`main_force_inflow` 资金信号检测器 |
| `risk.py` | 纯风险度量函数：`compute_atr`（Wilder 平滑）、`compute_max_drawdown`（滚动峰值回撤）、`compute_beta`（相对基准 OLS 斜率，重叠样本 ≥30 才输出），确定性、可单测 |
| `valuation_data.py` | 批量抓取估值与质量数据：东财 datacenter `RPT_VALUEANALYSIS_DET`（PE_TTM/PB/PS_TTM/PCF/PEG/总市值 + 抓取上限按最大分位窗口 3 年 ≈800 会话，PE/PB 3 年分位为主、1 年为辅，抓取时计算，原始逐日序列不持久化）与 `RPT_F10_FINANCE_MAINFINADATA`（ROE/毛利率/增速/现金流质量/杠杆 → `fundamental_quality_score` 0–100）；可选 tushare 兜底；按交易日缓存 + per-symbol 错误隔离 |
| `events_data.py` | 事件与日历（2.9）：聚合限售解禁（东财 `RPT_LIFT_STOCK`，`fetch_lockup_records` 取 ALL 列含真实解禁比例 `free_ratio`）、龙虎榜（东财 `RPT_DAILYBILLBOARD_DETAILS`，`fetch_recent_board` 全市场近 5 个交易日按天回扫一次拉取）、业绩预告/股东增减持（Tushare `forecast`/`stk_holdertrade` 兜底，token 缺失自动降级记 error）→ `EventSnapshot`（未来 90 天事件按日期升序 + `event_risk_score` 综合风险分 0–100 + `high_risk_count`）；纯函数解析 + `compute_event_risk_score`（主导子分 + 每多一个 danger +5，封顶 100）+ TTL 缓存 + per-symbol 隔离，`load_events_data` 永不抛异常 |
| `sector_data.py` | 行业/板块强度：全市场东财行业板块排行（涨跌幅/主力净流入/涨跌家数/领涨股，复用 `src.tools.sector_tool.fetch_industry_board_ranking`）+ watchlist 按 `sector_board` 聚合（各配置周期平均收益/RPS `period_metrics`、资金流、ROE/毛利率/营收增速）+ 简版景气度评分（40/40/20 加权，纯函数可单测）；`load_sector_strength` 永不抛异常，失败降级；支持 `ranking` 入参复用（同交易日冻结全市场排行，watchlist 聚合仍现算） |
| `concept_data.py` | 题材/概念热度（2.15）：全市场概念板块排行（`src.tools.sector_tool.fetch_concept_board_ranking`，`fs=m:90+t:3`）+ watchlist 概念归属（`resolve_concept_boards` 减去单一行业板块），产出 `ConceptStrength` 列表与每股 `ConceptSnapshot`（所属概念/最热概念/热度分/涨停家数）；热度分 = 0.40×涨幅分位 + 0.30×主力净流入分位 + 0.30×涨停家数分位（横截面分位，缺维重归一化）；涨停家数复用 2.16 市场涨停池 `fetch_market_breadth` 的 `limit_up_rows`；纯函数 + `load_concept_data` 永不抛异常 |
| `sentiment_data.py` | 市场情绪温度计（2.16）：`fetch_market_breadth` 聚合全市场涨停/跌停/炸板池与连板天梯（东财 `push2ex getTopicZTPool` 主源 + tushare `limit_list_d`/`limit_step` 兜底，token 缺失降级 `unavailable`），`compute_sentiment_score`（涨停家数/炸板率/连板高度/昨日涨停溢价 0.30/0.25/0.25/0.20，固定参考刻度近似近 20 日分位）+ `load_market_sentiment` 产出 `MarketSentimentSnapshot`；`fetch_market_breadth` 同时供 2.15 复用涨停池 |
| `consensus_data.py` | 盈利预期/一致预期（2.17）：东财研报 `fetch_research_reports_data`（评级分布→`rating_score`、`consensus_eps_cur/next`、`analyst_count`）主源 + tushare `report_rc` 目标价/EPS 修正兜底；`ConsensusDataCache` TTL 1 天；`compute_forward_metrics` 由 engine 拿 `close` 后回填 `forward_pe`/`upside_pct`；`load_consensus_data` 永不抛异常 |
| `chip_data.py` | 筹码集中度/机构动向（2.18）：东财股东户数 `fetch_shareholder_count` 主源 + 北向 `hk_hold`/公募 `fund_portfolio` tushare 兜底（季度口径）；`compute_chip_concentration_score`（户数下降/户均上升/机构增持 0.40/0.30/0.30）+ `compute_holder_trend`（连续下降=吸筹）；`ChipDataCache` TTL 7 天；`load_chip_data` 永不抛异常 |
| `_convert.py` | 共享标量转换助手（`to_float`/`to_float_div`/`dashed_date`），被 `capital_data`、`valuation_data`、事件与 P1.5 加载器复用 |
| `engine.py` | `StockTrackerEngine.refresh`：取 OHLCV，拉取资金流向+融资融券+估值质量+事件日历数据，解析沪深300基准与行业板块，计算周期指标与 RPS 分位，挂载 `RiskMetrics`（ATR/回撤/Beta/止损参考价，Beta 复用 RPS 基准帧）、`ValuationSnapshot` 与 `EventSnapshot`（`events`，同日复用前快照种子缓存），跑检测器，按元数据生成排名（含 `rps_market_{period}`/`rps_sector_{period}`）与跨日 diff；refresh 尾部调用 `_compute_sector_strength` 生成 `sectors` 并回填每股 `sector_strength_rank`；同交易日重复 refresh 复用上一快照的 `sector_board`（板块映射，`_resolve_sector_boards`）与全市场排行（`_cached_sector_ranking`），只对缺失标的调东财，减少节流串行请求；P1.5 新增 `_resolve_concept_boards`/`_cached_concept_ranking` 镜像行业板块冻结复用，refresh 一次 `fetch_market_breadth` 同时供情绪与概念，逐 symbol 加载筹码/一致预期（独立 TTL 缓存，`_seed_caches_from_previous` 种子），尾部 `compute_forward_metrics` 回填 forward PE/上行空间 |
| `capital_data.py` | 批量抓取个股资金流向（东财分单）与融资融券数据，按交易日 + namespace 缓存，per-symbol 错误隔离，返回历史序列 `fund_flow.history` 与 `margin.history` |
| `names.py` | 经腾讯行情接口解析中文名 |
| `store.py` | `TrackerStore`：原子 JSON 文件存储；`list_analyses` 之外提供 `list_analysis_envelopes`（含完整 report，供 track record 回放） |
| `analyzer.py` | `run_analysis` 包装 `ChatLLM` 产出**结构化 `AnalysisReport`**（2.10）：symbol 序列化注入资金流 `capital`/风险 `risk`/估值 `valuation`/事件日历 `events`/行业板块与强度排名/题材热度 `concept`/一致预期 `consensus`/筹码 `chip`，context 注入 `snapshot.sectors` 行业强度与 `snapshot.concepts` 概念强度、`snapshot.market_sentiment` 市场情绪；报告固定全维度分析（技术面/资金面/估值质量/风险/事件日历/行业背景），可选 `user_prompt` 追加补充指令（无 focus 概念）；每条 symbol 输出 `action`(buy/hold/reduce/avoid)/`confidence`(0–100)/`entry_zone`/`target_zone`/`stop_loss`/`reduce_trigger`/`track_metrics`；`_normalize_report` 宽容归一化（旧 `recommendation`→action 映射、confidence 字符串→数值、PriceZone 兼容 dict/list/单值） |
| `track_record.py` | 纯函数 `build_track_record`：遍历持久化分析的 symbol，把含价位锚点（entry/target/stop）的条目建成可验证预测，用最新 `close` 分类 `pending/active/hit_target/stopped_out`（2.10，只读，不算胜率） |

- **路由**（挂载于 `/api/stock-tracker/`）：`settings` GET/PUT（含 `refresh_interval_seconds`、`detail_card_count`）、`signals` GET（信号元数据）、`GET /`、`history`、`quotes` GET（轻量实时行情）、`refresh` POST（后台执行：立即返回，经 `refresh-status` 轮询进度，失败写入 `refresh.error`）、`refresh-status`、`analyze` POST/GET、`analyze/history`、`analyze/track-record`、`analyze/{id}` GET/DELETE。
- **扩展方式**：新增信号只需在 `signals.py` 写一个 `SignalDetector` 子类并 `register_detector`；若信号依赖资金数据，在 `capital_data.py` 中返回对应字段，engine 会自动写入 `SymbolSnapshot.capital`，无需改路由。
- **改进计划**：详见 [`docs/plans/stock-tracker-improvement-plan.md`](plans/stock-tracker-improvement-plan.md)。
- **测试**：`tests/stock_tracker/test_{models,signals,engine,risk,store,names,analyzer,track_record,capital_data,valuation_data,sector_data,events_data,events_tools,concept_data,sentiment_data,consensus_data,chip_data,concept_tools}.py` + `tests/api/test_stock_tracker_routes.py`；前端 `__tests__/` 增 `ConceptHeatCard`/`ConsensusCard`/`ChipCard`/`MarketSentimentBar` 冒烟测试。

## 六、前端 frontend/

- 栈：React 19 + TS + Vite 8 + Tailwind（shadcn 风格 CSS 变量 token、暗色模式 class 切换、`@tailwindcss/typography`）；无第三方 UI 库。
- 库：`react-router` v8（`router.tsx` `createBrowserRouter`）、Zustand、i18next（`en.json`/`zh-CN.json`）、ECharts、`react-markdown`+remark/rehype+KaTeX+highlight.js、`lucide-react`、`sonner`。

| 目录 | 说明 |
|------|------|
| `src/pages/` | 每路由一文件：`Home/Agent/Portfolio/Runtime/RunDetail/Reports/Compare/Correlation/AlphaZoo/OptionsLab/Scheduled/Settings/StockTracker`（+ `agentToolTimeline.ts`） |
| `src/components/` | `chat/`(对话/流式/swarm)、`charts/`、`run/`、`portfolio/`、`options/`、`settings/`、`stock-tracker/`、`layout/`、`common/`。`stock-tracker/` 详情卡：`MarginChartCard`/`FundFlowChartCard`/`RpsChartCard`/`RiskMetricsCard`/`ValuationCard`/`EventTimelineCard`/`ConceptHeatCard`(2.15)/`ConsensusCard`(2.17)/`ChipCard`(2.18)（`detail_card_count` 控制显示数量，默认 9）；`MarketSentimentBar`(2.16) 全宽市场情绪温度条；`SectorStrengthBoard` 支持「行业|概念」tab（概念 tab 以涨停家数替代景气度）；`TrackerTable` 增「概念」列（最热概念 chip） |
| `src/stores/` | Zustand：`agent.ts`、`stockTrackerAnalysis.ts` |
| `src/hooks/` | `useSSE.ts`(SSE 流式)、`useDarkMode.ts`、`useChartLifecycle.ts` |
| `src/lib/` | `api.ts`/`apiAuth.ts`(REST 客户端) + 领域助手（formatters/indicators/options/positions/markdown/stockTracker/runReports/navVisibility 等） |
| `src/i18n/` `src/types/` `src/tests/` `src/__tests__/` | 国际化 / 类型 / 测试 |

- **通信**：相对 URL REST（`BASE=""`）；Vite dev 代理固定路径列表（`/auth` `/sessions` `/runs` `/live` `/qveris` `/alpha` 等）到 `http://127.0.0.1:8899`（`VITE_API_URL` 覆盖）。**流式为 SSE**（`EventSource`，自动重连 + Last-Event-ID 续传），无 WebSocket；鉴权用 `apiAuth.ts` 注入请求 ticket。
- **脚本**：`dev`=vite(5899)、`build`=`tsc -b && vite build`、`test`/`test:run`/`test:coverage`(Vitest)。

## 七、桌面 desktop/electron/

- Electron 43 + TS 壳（社区非官方构建）：包装既有 FastAPI 后端 + 已构建 React 前端为 Windows 原生应用。
- `src/main.ts`/`preload.ts`/`backend-manager.ts`：spawn 自有的 `vibe-trading serve` 进程，绑定随机回环端口 + 每次启动 256bit 鉴权 secret，单实例 + 父进程退出清理，`safeStorage` 存凭证，electron-builder 打包（NSIS 安装包，`scripts/` 构建/smoke/打包）。后端以 `extraResources` 打包。
- 仅一个 Electron 宿主，不含新产品逻辑。

## 八、运行 / 部署 / 测试

- **入口**：
  - `vibe-trading`（`cli:main`）— 交互式 CLI/TUI；子命令 `serve/run/mcp/sessions/swarm/alpha/hypothesis` 委托 `cli/_legacy.py`。
  - `vibe-trading-mcp`（`mcp_server:main`）— MCP 服务（74 个只读研究工具；`stdio` 默认、`--transport sse`/`http`，http 端点 `/mcp`；不暴露下单工具）。
- **启动**：`scripts/dev up`（后端 8899 + 前端 5899）；`start-web.bat`（每次启动先 `npm run build` 再静态托管前端于 8899）；`docker-compose.yml`（后端 127.0.0.1:8899）。
- **端口**：后端 8899，前端 dev 5899。
- **测试**：`pytest`（根目录；`pythonpath=["agent"]`、`testpaths=["agent/tests"]`）；`tests/` 下 `api/ factors/ memory/ quantlib/ stock_tracker/ fixtures/`；marker `unit`/`integration`。
- **lint**：`ruff`（E/F/W，line-length 120；`agent/src/factors/zoo/**` 忽略 F401）。
- **配置**：`agent/.env`（python-dotenv），schema `src/config/env_schema.py`；关键 env `LANGCHAIN_PROVIDER`/`LANGCHAIN_MODEL_NAME`/各 `*_API_KEY`/`*_BASE_URL`/`TUSHARE_TOKEN`/`API_AUTH_KEY`/`CORS_ORIGINS`/`VIBE_TRADING_HOME`/`VT_MEMORY`/`VIBE_TRADING_ENABLE_SCHEDULER`/`VIBE_TRADING_ENABLE_SHELL_TOOLS`。
