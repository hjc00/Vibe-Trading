# Vibe-Trading — 项目导航

> 本文件是 AI 上下文索引，每次会话自动加载。改动代码后请同步更新本文件或 [docs/PROJECT_INDEX.md](docs/PROJECT_INDEX.md)（见文末「文档同步约定」）。
> 最后更新：2026-09-03（已落地 2.2 RPS、2.3 风险指标、2.5 行业强度看板、2.6 估值与质量、2.15 题材热度、2.16 市场情绪、2.17 一致预期、2.18 筹码集中度、量能对比 VolumeCard、周期价量K线与单/双列宽切换）

## 项目定位

- `vibe-trading-ai` v0.1.14 —— 自然语言驱动的金融研究 AI Agent（带回测）。
- 上游 `HKUDS/Vibe-Trading`，当前维护于个人 fork，**只推 origin，不向上游开 PR**。
- 技术栈：
  - 后端：Python 3.11+ / FastAPI / LangChain / LangGraph / DuckDB
  - 前端：React 19 + TypeScript + Vite + Tailwind
  - 桌面：Electron（仅包装后端 + 前端，无独立产品逻辑）

## 当前活跃功能

- A股多周期股票追踪（`stock_tracker`）：[agent/src/stock_tracker/](agent/src/stock_tracker/) + [agent/src/api/stock_tracker_routes.py](agent/src/api/stock_tracker_routes.py)

## 顶层目录

| 目录 | 说明 |
|------|------|
| `agent/` | Python 后端（主代码库，见下方模块地图） |
| `frontend/` | Vite + React 前端 |
| `desktop/electron/` | Electron 桌面壳 |
| `data/` | 运行时产物（含 `stock_tracker/` 快照） |
| `docs/` | 文档索引与改进计划（含 `PROJECT_INDEX.md`、`plans/`） |
| `artifacts/`、`bear_advocate/`、`factor_out/`、`factor_output/` | 生成 / scratch 目录 |
| `assets/` | 仓库媒体（截图 / 图标 / demo） |
| `wiki/` | 独立文档站源码（vibetrading.wiki） |
| `.devcontainer/`、`.github/` | dev 容器 / CI 配置 |

## 运行 & 测试

### 运行

- 开发（前后端一起）：`scripts/dev up` → 后端 127.0.0.1:8899 + 前端 dev 5899
- 一键（后端静态托管前端）：`start-web.bat` → 每次启动先 `npm run build` 再 `vibe-trading serve --port 8899`
- Docker：`docker-compose.yml`（后端 127.0.0.1:8899）

### 测试 & lint

- `pytest`（在仓库根运行；`pythonpath=["agent"]`、`testpaths=["agent/tests"]`、marker `unit`/`integration`）
- `ruff`（select E/F/W，line-length 120；`agent/src/factors/zoo/**` 忽略 F401）

## 后端 src/ 模块地图

> 一行一个子模块；逐模块详解见 [docs/PROJECT_INDEX.md](docs/PROJECT_INDEX.md#三后端-agentsrc-逐模块)。

**核心运行时**：`agent`(ReAct AgentLoop / 工具注册)、`core`(Runner + RunStateStore)、`session`(会话 / SSE 持久化)、`providers`(LLM 抽象，~25 家供应商)、`tools`(工具自动发现注册表)、`memory`(跨会话记忆)、`governance`(运行清单 + 哈希链账本)、`security`(输出净化)、`config`(env 加载 / schema)、`preflight.py`(启动连通检查)、`goal`+`hypotheses`(研究目标 / 假设运行时)、`swarm`(多智能体团队)

**数据与数学**：`market_data.py`(共享行情助手)、`openbb_bridge`(OpenBB 桥接)、`quantlib`(金融数学原语)、`entities`(非日频持仓现金流)、`factors`(Alpha Zoo，5 家族 × 460+ alpha)

**组合与交易**：`portfolio`(只读多券商聚合)、`trading`(连接器 profiles / 操作)、`live`(有界自主实盘)、`shadow_account`(盈利模式 → 回测 → 渲染)

**扩展与技能**：`skills`(90 个打包技能)、`strategy_discovery`(Alpha Zoo + SDM 实证门控 facade)、`strategy_store`(策略开发管理器 + 衰减)、`scheduled_research`(定时研究 + playbooks)、`ui_services.py`(前端 run 分析整形)、`stock_tracker`(A股多周期追踪，活跃功能；已支持资金流信号、个股相对强弱 RPS、风险指标、行业强度看板 `sector_data.py`、估值质量评分、题材/概念热度 `concept_data.py`、市场情绪温度计 `sentiment_data.py`、盈利预期/一致预期 `consensus_data.py`、筹码集中度 `chip_data.py` 与量能对比 `PeriodMetrics` 量能字段 + 前端 `VolumeCard`(量能文本) / `VolumeChartCard`(周期价量K线+量，单/双列宽表头切换))

**通道与集成**：`channels`(多 IM 插件适配)、`channelsui`(WebUI 兼容)、`api`(HTTP 路由包)、`utils`(media_decode)

## 关键入口

- [agent/api_server.py](agent/api_server.py) — FastAPI 装配，注册 `src/api/*` 路由
- [agent/cli/main.py](agent/cli/main.py) — CLI 门面，`serve/run/mcp/swarm/alpha/hypothesis` 委托 `_legacy.py`
- [agent/mcp_server.py](agent/mcp_server.py) — 74 个只读研究工具，stdio/SSE/http 三种传输
- [agent/SKILL.md](agent/SKILL.md) — 包级清单（frontmatter + 配置说明）

## 约定

- **只推 fork**：`git push origin <branch>`，不向上游开 PR。
- **新功能落位**：`src/<module>/` + `src/api/<module>_routes.py` + `tests/<module>/`（+ `tests/api/test_<module>_routes.py`）。
- **测试 marker**：`unit`（快速、无网络）/ `integration`（可能联网）。
- **配置**：根目录 `pyproject.toml`（`package-dir="agent"`）；后端 env 在 `agent/.env`（schema 见 `src/config/env_schema.py`）。

## 文档同步约定

> 每次改代码后必须执行，防止文档与代码脱节。

1. 改到 `agent/src/<module>/`、`frontend/src/` 等模块的代码，若**职责 / 结构 / 接口**发生变化 → 同步更新 [docs/PROJECT_INDEX.md](docs/PROJECT_INDEX.md) 中对应模块的说明。
2. 改动涉及**顶层目录结构、新增 / 删除 `src/` 子模块、入口文件、运行 / 测试方式** → 同步更新本文件与 [docs/PROJECT_INDEX.md](docs/PROJECT_INDEX.md) 的目录树。
3. 纯 bug 修复 / 内部重构、不影响模块职责描述的 → 可跳过文档更新，但需在交付说明里注明「无需更新文档」。

## 深度文档指针

- [docs/PROJECT_INDEX.md](docs/PROJECT_INDEX.md) — 逐模块深度索引（按需读取）
- [README.md](README.md)（含 `📁 Project Structure` 章节）/ [README_zh.md](README_zh.md) — 权威结构文档，以 README 为准，不复制其内容
- [AGENT_CONTRIBUTOR_GUIDE.md](AGENT_CONTRIBUTOR_GUIDE.md) — agent 侧安全 / 验证约定（高危面、测试提示、安全规则）
