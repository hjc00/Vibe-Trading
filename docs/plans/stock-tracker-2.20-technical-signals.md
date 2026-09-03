# stock-tracker 2.20 技术指标信号扩展 + 技术指标子图

> 关联主计划：[stock-tracker-improvement-plan.md](stock-tracker-improvement-plan.md)（编号 2.20）
> 创建时间：2026-09-03
> 最后更新：2026-09-04（v2：技术指标子图纳入本期）
> 状态：实施中

## 一、背景与目的

**用户画像**：中长线（持有 3–6 个月）+ 短线交易 + 熟练技术指标。

**现状**：`stock_tracker` 已有 7 个可插拔信号检测器（放量、突破、均线排列、RSI、融资扩张、净流入脉冲、主力连续流入），但**缺 A 股技术交易者的基础指标**——MACD、KDJ、布林带、背离；且前端**无价格 K 线图**，只有周期收益柱状图，指标无从可视化。

**本次目标**：新增 5 个信号 + 1 张技术指标子图（MACD/KDJ/布林带/背离标注），把看板从「看」补成「决策」。

| 信号 | 核心用途 |
|------|---------|
| MACD | 中长线「方向罗盘」——趋势方向与拐点 |
| 背离 divergence | 中长线「逃顶/抄底雷达」——趋势反转提前量 |
| KDJ | 短线「买卖点触发器」——超买超卖择时 |
| 布林 %B | 中长线「握力器」——判断强势股该不该继续持有 |
| 布林挤压 | 「埋伏雷达」——大级别突破前兆 |
| 技术指标子图 | 上述信号的可视化：K线+布林+均线 / 成交量 / MACD / KDJ 四 pane 联动 |

## 二、范围界定

**本次交付**：5 个信号（后端 detector + 前端 label + 单测）+ 技术指标子图（后端指标序列 + 前端图表）+ 顺手修前端 label 遗漏 + 数据拉取窗口提升。

**明确后置（本次不做）**：
- **周期改造**（2.4 多周期共振的一部分）：`periods` 加 120 日、`compute_mas` 加 ma120/ma250、`ma_alignment` 五线升级。理由：用户确认「周期后续加」。
- 量价背离（价涨量缩）独立信号：本次背离以 **MACD DIF 指标背离**为主，量能背离留作后续轻量信号。
- 决策闭环（仓位建议 / 信号回测胜率 / 价格告警）：分别归属 2.3 延伸、2.7、2.12。

## 三、三个架构决策（已与用户确认）

1. **指标计算函数化**：新增 `agent/src/stock_tracker/indicators.py`，把 MACD/KDJ/布林带/背离检测抽成纯函数，detector 与序列化共用同一份实现，保证 badge 与子图数值严格一致。
2. **数据拉取窗口提升**：`_BUFFER_DAYS` 90 → 225（引入 `_PRICE_BARS=130`），否则布林挤压 `squeeze_lookback=125` 永远数据不足、子图无足够 K 线。
3. **序列化用 per-bar 对象**：新增 `IndicatorBar`/`IndicatorSeries`，挂到 `SymbolSnapshot.indicators`，前端按现有 history 模式消费。

## 四、信号详细设计

> 所有阈值走 `TrackerThresholds` 的 `extra="allow"` 机制（`thresholds.get(name, default)`），照 `net_inflow_spike` 的写法，**零改动 `TrackerThresholds` 类型字段**。

### 4.1 MACD — `macd`

- **公式**：`DIF = EMA(close,12) - EMA(close,26)`；`DEA = EMA(DIF,9)`；`histogram = 2*(DIF-DEA)`（国标 MACD 柱）。
- **触发**：金叉（DIF 上穿 DEA）：零轴上判 `STRONG`、零轴下判 `TRIGGERED`（均 bullish）；死叉（DIF 下穿 DEA）：`TRIGGERED`（bearish）。
- **参数**：`macd_fast=12`、`macd_slow=26`、`macd_signal=9`。
- **输出**：`value = DIF-DEA`（round 4）、`format=raw`、`category=trend`、`direction=both`、`ranking_extractor=abs(value)`。
- **降级**：`len(df) < macd_slow + macd_signal` 时 `triggered=False`。

### 4.2 背离 — `divergence`

- **目的**：价创新高但 DIF 未创新高 = 顶背离（减仓）；价创新低但 DIF 未创新低 = 底背离（埋伏）。
- **算法**：复用 MACD 的 DIF 序列；`find_swing_points(series, pivot)` 取局部极值；`lookback = max(period, divergence_min_lookback)`；取窗口内最近两个摆动高点/低点，容差 `divergence_tolerance` 过滤噪声。
- **参数**：`divergence_min_lookback=40`、`divergence_pivot=3`、`divergence_tolerance=0.002`。
- **输出**：`value = 背离强度`（顶负底正，round 4）、`format=raw`、`category=momentum`、`direction=both`、`ranking_extractor=abs(value)`。
- **降级**：摆动高点或低点不足 2 个 → `triggered=False` + "insufficient swing points"。
- **子图标注**：`find_divergence` 同时返回两个摆动点坐标，序列化成 `DivergenceMark`，前端在主图连价高点/DIF 点。

### 4.3 KDJ — `kdj`

- **公式**（默认 9,3,3）：`RSV = (close-LLV9)/(HHV9-LLV9)*100`；`K = 2/3*K_prev + 1/3*RSV`（初始 50）；`D = 2/3*D_prev + 1/3*K`；`J = 3K-2D`。
- **触发**：低位金叉（K 上穿 D 且 K<20）→ bullish `STRONG`；金叉 → bullish `TRIGGERED`；高位死叉（K 下穿 D 且 K>80）→ bearish `STRONG`；死叉 → bearish `TRIGGERED`；J 钝化作为描述附加。
- **参数**：`kdj_n=9`（K/D 平滑系数 1/3 固定）。
- **输出**：`value = J`（round 1）、`format=raw`、`category=momentum`、`direction=both`、`ranking_extractor=abs(J-50)`。
- **降级**：`len(df) < kdj_n + 1` 时 `triggered=False`；HHV==LLV 平坦段 RSV 置 50 防除零。

### 4.4 布林 %B — `bollinger_pct_b`

- **公式**：`mid = MA(close,n)`、`upper/lower = mid ± k*std(close,n)`、`%B = (close-lower)/(upper-lower)`。
- **触发**：`%B > 1` → bullish `STRONG`；`%B < 0` → bearish `TRIGGERED`。
- **参数**：`bb_n=20`、`bb_k=2.0`。
- **输出**：`value = %B`（round 3）、`format=raw`、`category=momentum`、`direction=both`、`ranking_extractor=abs(%B-0.5)`。
- **降级**：`upper == lower` 或 `len(df) < bb_n` 时 `triggered=False`。

### 4.5 布林挤压 — `bollinger_squeeze`

- **公式**：`bandwidth = (upper-lower)/mid`；当前 bandwidth 在近 `squeeze_lookback` 日的分位。
- **触发**：分位 < `squeeze_pctile` → 挤压状态（`STRONG`，neutral）；前一日挤压 + 今日带宽放大 → 开口（突破前兆）。
- **参数**：`bb_n=20`、`bb_k=2.0`、`squeeze_lookback=125`、`squeeze_pctile=0.05`。
  - **注意**：`bb_n`/`bb_k` 复用 %B 的参数（`thresholds.get`），**不在 `meta.params` 重复声明**，避免配置面板重复输入框。
- **输出**：`value = 带宽分位`（round 4）、`format=percent`、`category=volatility`、`direction=neutral`、`ranking_enabled=False`。
- **默认关**（不进 `DEFAULT_SIGNALS`，用户手动开启）。

## 五、方向判定（badge 正确着色的前提）

给 `SignalValue` 加可选字段 `direction: Optional[Literal["bullish","bearish","neutral"]] = None`，新 detector 显式赋值；前端 badge 用 `signal.direction ?? meta.direction ?? 描述启发式` 三级回退。现有英文关键词启发式对「MACD 零轴下金叉」会误判（`below` 命中判空），必须加此字段。

## 六、技术指标子图设计

### 6.1 后端序列化

```python
class IndicatorBar(BaseModel):
    date; open/high/low/close/volume
    ma5/ma10/ma20/ma60
    dif/dea/macd_hist
    k/d/j
    bb_upper/bb_mid/bb_lower/pct_b/bandwidth

class DivergenceMark(BaseModel):
    kind: "top" | "bottom"
    price_hi_idx/price_lo_idx/dif_hi_idx/dif_lo_idx

class IndicatorSeries(BaseModel):
    bars: List[IndicatorBar]
    divergence_marks: List[DivergenceMark]
```

`SymbolSnapshot` 加 `indicators: Optional[IndicatorSeries]`，`_analyze_symbol` 填尾部 `_PRICE_BARS=130` 根。金叉/死叉交叉点不序列化，由前端从 dif/dea、k/d 差分求。

### 6.2 前端 `IndicatorChartCard.tsx`

- ECharts 单实例 4 grid（纵向堆叠，x 轴共用）：主图(K线+布林+均线) / 成交量 / MACD / KDJ。
- 复用 `ChartCardHeader` + `useChartLifecycle` + `getChartTheme`。
- 交互：指标开关 chips（布林带/均线/MACD/KDJ 独立显隐）、十字光标 `axisPointer.link` 四图联动、`dataZoom` inside+slider 联动、顶/底背离 `markLine` 连线、金叉死叉 `markPoint`、%B 破轨 K 线描边、挤压区间 `markArea`。

## 七、改动文件清单

### 后端

| 文件 | 改动 |
|------|------|
| `agent/src/stock_tracker/indicators.py` | **新增**：`compute_macd`/`compute_kdj`/`compute_bollinger`/`find_swing_points`/`find_divergence` |
| `agent/src/stock_tracker/signals.py` | 新增 5 个 detector（复用 indicators）+ `SignalValue.direction` 赋值 + `register_detector(...)` ×5 |
| `agent/src/stock_tracker/models.py` | `IndicatorBar`/`DivergenceMark`/`IndicatorSeries` + `SignalValue.direction` + `DEFAULT_SIGNALS` 加 4 个 + `SymbolSnapshot.indicators` |
| `agent/src/stock_tracker/engine.py` | `_PRICE_BARS=130` + `_BUFFER_DAYS` 提升 + `_analyze_symbol` 填充 `indicators` |
| `agent/src/stock_tracker/__init__.py` | 导出新 detector 类 + indicators 纯函数 |

**不改**：`compute_mas`、`ma_alignment`、`PeriodMetrics`、`TrackerThresholds` 类型字段、`analyzer.py`（`_serialize_symbol` 已 dump `period_signals`；`indicators` 走 `model_dump` 自动透传，但 LLM prompt 不必注入指标序列）。

### 前端

| 文件 | 改动 |
|------|------|
| `frontend/src/lib/api.ts` | `SignalValue.direction` + `IndicatorBar`/`IndicatorSeries`/`SymbolSnapshot.indicators` 类型 |
| `frontend/src/lib/stockTracker.ts` | `SIGNAL_LABEL_KEYS` 加 5 条 + 补 `net_inflow_spike`/`main_force_inflow` 2 条（共 7 条） |
| `frontend/src/components/stock-tracker/SignalBadge.tsx` | 方向三级回退 |
| `frontend/src/components/stock-tracker/IndicatorChartCard.tsx` | **新增**技术指标子图 |
| `frontend/src/pages/StockTracker.tsx` | 详情区接入 `IndicatorChartCard` |
| `frontend/src/i18n/locales/zh-CN.json` / `en.json` | 信号 label + 参数 label + 图表文案 |

### 测试

| 文件 | 改动 |
|------|------|
| `agent/tests/stock_tracker/test_indicators.py` | **新增**指标纯函数单测 |
| `agent/tests/stock_tracker/test_signals.py` | 每个新信号加构造数据用例 + `direction` 断言 |

## 八、实施顺序

1. `indicators.py` 纯函数 + 单测。
2. `signals.py` 5 个 detector 改用纯函数 + `register_detector`。
3. `models.py` 序列化模型 + `DEFAULT_SIGNALS` + `direction`。
4. `engine.py` 窗口提升 + 填充 `indicators`。
5. `__init__.py` 导出。
6. 前端类型 + label + badge 方向回退 + `IndicatorChartCard` + 接入。
7. i18n。
8. 单测 + `docs/PROJECT_INDEX.md` 同步。

## 九、测试清单

| 层 | 用例 |
|----|------|
| `indicators.py` | compute_macd 金叉/死叉数值、compute_kdj 初始值/平坦段、compute_bollinger 上下轨、find_swing_points、find_divergence 顶/底/无 |
| detector | 金叉/死叉、零轴上金叉 STRONG、背离顶/底/无、KDJ 低位金叉/高位死叉、%B 破轨、挤压分位、数据不足降级、`SignalValue.direction` |
| 序列化 | `_analyze_symbol` 产出 `indicators` 非空、bars 长度≈130、divergence_marks 与 detector 触发一致 |
| 前端 | `IndicatorChartCard` mock symbol 渲染、开关切换、`SignalBadge` 方向回退 |

**验证命令**：`pytest agent/tests/stock_tracker/` 全绿 + `ruff check`（line-length 120）+ 前端 `npm run build`。

## 十、验收标准

- [ ] 5 个新信号注册进 detector registry，`list_detector_names()` 能返回。
- [ ] 每个信号触发逻辑有单测覆盖，降级路径（数据不足）不抛异常。
- [ ] `SignalValue.direction` 正确、前端 badge 涨/跌/中性配色正确。
- [ ] `SymbolSnapshot.indicators` 序列化非空，前端技术指标子图渲染 K线/布林/均线/成交量/MACD/KDJ，背离/金叉死叉标注正确。
- [ ] 前端表格/榜单显示新信号中文标签，label 遗漏的 2 个资金信号恢复正常。
- [ ] `DEFAULT_SIGNALS` 含 `macd`/`divergence`/`kdj`/`bollinger_pct_b`，`bollinger_squeeze` 可手动开启。
- [ ] 布林挤压在窗口提升后不再因数据不足永久降级。
- [ ] 周期改造（120 日/半年线/五线排列）保持后置，本批不触碰相关代码。

## 十一、风险与注意

- **信号数量 7 → 12**：只进 badge 和榜单，不影响 `detail_card_count` 与布局。
- **窗口提升的连锁影响**：`_BUFFER_DAYS` 90→225 让每只股票 fetch 变大、snapshot 持久化变大，幅度可控；RPS/benchmark 同步放大无副作用。
- **挤压信号与窗口耦合**：`squeeze_lookback=125` 必须 ≤ `_PRICE_BARS`，用同一常量或注释联动。
- **背离误报**：短线周期用 `divergence_min_lookback=40` 下限防噪声；容差过滤毛刺；摆动点不足自动降级。
- **MACD 预热**：慢线 26 + 信号线 9，DIF 需 ~35 根 K 才稳定，数据不足时降级。
- **与 2.4 的关系**：本批信号已为「多周期共振」铺路（divergence 的 `max(period, min_lookback)` 联动），后续 2.4 落地长周期时直接受益。
