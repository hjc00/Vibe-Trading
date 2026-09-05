import { ChevronDown, Circle, CircleDot, LineChart, Loader2, Play, Plus, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useCardCollapse } from "@/hooks/useCardCollapse";
import { safeGet, safeSet } from "@/lib/storage";
import {
  api,
  type BacktestCondition,
  type BacktestPreset,
  type BacktestPrimitiveCategory,
  type BacktestPrimitiveMeta,
  type BacktestRule,
  type BacktestSnapshot,
  type BacktestSpec,
  type BacktestTradePoint,
  type SymbolSnapshot,
} from "@/lib/api";
import { ChartCardHeader } from "./ChartCardHeader";
import { PrimitiveMenu } from "./PrimitiveMenu";

interface BacktestCardProps {
  symbol: SymbolSnapshot | null;
  onHide?: () => void;
  /** Report the latest backtest's buy/sell fills for the page-level overlay. */
  onBacktestResult?: (result: { code: string; trades: BacktestTradePoint[] } | null) => void;
  /** Render without its own card border/header (embed inside a shared card). */
  bare?: boolean;
}

// Default window ≈ the last 60 A股 trading sessions (≈92 calendar days once
// weekends and holidays are counted) — mirrors the backend default.
const DEFAULT_LOOKBACK_DAYS = 92;
type RuleKey = "buy" | "sell";

// Persisted rule-builder settings (spec + dates + exits + disable-sell), so a
// strategy the user built survives reloads, matching the other tracker config.
const BACKTEST_SETTINGS_KEY = "stockTracker.backtestSettings.v1";

interface BacktestStoredSettings {
  presetId: string;
  spec: BacktestSpec;
  sellDisabled: boolean;
  multiBuys: boolean;
  takeProfitPct: string;
  stopLossPct: string;
  start?: string;
  end?: string;
}

function isBacktestSpec(value: unknown): value is BacktestSpec {
  const spec = value as Partial<BacktestSpec> | null;
  return Boolean(
    spec &&
      typeof spec === "object" &&
      spec.buy &&
      spec.sell &&
      Array.isArray(spec.buy.conditions) &&
      Array.isArray(spec.sell.conditions),
  );
}

function isoDaysAgo(days: number): string {
  const d = new Date(Date.now() - days * 86400_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function signedPct(value: number | null | undefined): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

function plainPct(value: number | null | undefined): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function num(value: number | null | undefined, digits = 2): string {
  if (value === undefined || value === null || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

function cloneSpec(spec: BacktestSpec): BacktestSpec {
  return JSON.parse(JSON.stringify(spec)) as BacktestSpec;
}

function newCondition(primitive: BacktestPrimitiveMeta): BacktestCondition {
  const params: Record<string, number> = {};
  for (const p of primitive.params) params[p.key] = p.default;
  return { primitive: primitive.id, trigger: "state", params, enabled: true };
}

/** Serialize the editable spec: drop disabled conditions and the UI-only flag. */
function serializeRule(rule: BacktestRule): BacktestRule {
  return {
    ...rule,
    conditions: rule.conditions
      .filter((condition) => condition.enabled !== false)
      .map((condition) => {
        const clean = { ...condition };
        delete (clean as { enabled?: boolean }).enabled;
        return clean;
      }),
  };
}

interface MetricDef {
  labelKey: string;
  value: string;
  tone?: "up" | "down" | "plain";
}

/**
 * Single-symbol backtest card with a composable rule builder (单标的回测).
 *
 * A strategy is a buy rule + a sell rule; each rule combines signal-primitive
 * conditions (e.g. "fast MA above slow MA" with an edge_up trigger) using AND
 * or OR. Presets are one-click templates that pre-fill the rules. The run goes
 * through the real backtest engine and renders headline metrics plus the
 * strategy equity curve vs. buy-and-hold; the buy/sell B/S fills are reported
 * upward (onBacktestResult) so the page can overlay them on the standalone
 * technical chart (IndicatorChartCard) instead of duplicating a price panel.
 */
export function BacktestCard({ symbol, onHide, onBacktestResult, bare = false }: BacktestCardProps) {
  const { t } = useTranslation();
  const { collapsed, toggle } = useCardCollapse("backtest");
  const code = symbol?.code;

  const [primitives, setPrimitives] = useState<BacktestPrimitiveMeta[]>([]);
  const [categories, setCategories] = useState<BacktestPrimitiveCategory[]>([]);
  const [presets, setPresets] = useState<BacktestPreset[]>([]);
  const [presetId, setPresetId] = useState("");
  const [label, setLabel] = useState("");
  const [spec, setSpec] = useState<BacktestSpec | null>(null);
  const [start, setStart] = useState(() => isoDaysAgo(DEFAULT_LOOKBACK_DAYS));
  const [end, setEnd] = useState(() => isoDaysAgo(0));
  // Optional exits (percent shown in the UI, decimals sent to the backend).
  const [takeProfitPct, setTakeProfitPct] = useState("");
  const [stopLossPct, setStopLossPct] = useState("");
  // Disable the sell rule entirely → buy once and hold to the end (长拿观察).
  const [sellDisabled, setSellDisabled] = useState(false);
  // True: re-open after each exit (multiple buys). False: one buy for the run.
  const [multiBuys, setMultiBuys] = useState(true);
  const [report, setReport] = useState<BacktestSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const requestSeq = useRef(0);
  const autoRanCode = useRef<string | null>(null);

  const primitiveById = useMemo(() => {
    const map = new Map<string, BacktestPrimitiveMeta>();
    for (const meta of primitives) map.set(meta.id, meta);
    return map;
  }, [primitives]);

  // Load the primitive catalog and preset templates once; restore persisted
  // rule-builder settings when present, else fall back to the first preset.
  useEffect(() => {
    let alive = true;
    Promise.all([api.getStockTrackerBacktestPrimitives(), api.getStockTrackerBacktestPresets()])
      .then(([primsRes, presetRes]) => {
        if (!alive) return;
        setPrimitives(primsRes.primitives);
        setCategories(primsRes.categories ?? []);
        setPresets(presetRes.presets);
        const raw = safeGet(BACKTEST_SETTINGS_KEY);
        let saved: BacktestStoredSettings | null = null;
        if (raw) {
          try {
            saved = JSON.parse(raw) as BacktestStoredSettings;
          } catch {
            saved = null;
          }
        }
        if (saved && isBacktestSpec(saved.spec)) {
          const matched = presetRes.presets.find((preset) => preset.id === saved.presetId);
          if (matched) {
            setPresetId(matched.id);
            setLabel(matched.label);
          } else {
            setPresetId("");
            setLabel(t("stockTracker.backtestCustom"));
          }
          setSpec(cloneSpec(saved.spec));
          setSellDisabled(saved.sellDisabled === true);
          setMultiBuys(saved.multiBuys !== false);
          setTakeProfitPct(typeof saved.takeProfitPct === "string" ? saved.takeProfitPct : "");
          setStopLossPct(typeof saved.stopLossPct === "string" ? saved.stopLossPct : "");
          if (saved.start) setStart(saved.start);
          if (saved.end) setEnd(saved.end);
        } else if (presetRes.presets.length > 0) {
          const first = presetRes.presets[0];
          setPresetId(first.id);
          setLabel(first.label);
          setSpec(cloneSpec(first.spec));
        }
      })
      .catch(() => {
        if (alive) {
          setPrimitives([]);
          setCategories([]);
          setPresets([]);
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  // Persist the rule builder + exits + dates whenever they change.
  useEffect(() => {
    if (!spec) return;
    const stored: BacktestStoredSettings = {
      presetId,
      spec,
      sellDisabled,
      multiBuys,
      takeProfitPct,
      stopLossPct,
      start,
      end,
    };
    safeSet(BACKTEST_SETTINGS_KEY, JSON.stringify(stored));
  }, [spec, presetId, sellDisabled, multiBuys, takeProfitPct, stopLossPct, start, end]);

  const markCustom = useCallback(() => {
    setPresetId("");
    setLabel(t("stockTracker.backtestCustom"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setRule = useCallback((ruleKey: RuleKey, rule: BacktestRule) => {
    setSpec((prev) => {
      if (!prev) return prev;
      return { ...prev, [ruleKey]: rule };
    });
  }, []);

  const setMode = useCallback(
    (ruleKey: RuleKey, mode: "and" | "or") => {
      const current = spec?.[ruleKey];
      if (!current) return;
      markCustom();
      setRule(ruleKey, { ...current, mode });
    },
    [spec, setRule, markCustom],
  );

  const addCondition = useCallback(
    (ruleKey: RuleKey) => {
      const meta = primitives[0] ?? null;
      if (!meta || !spec) return;
      markCustom();
      const rule = spec[ruleKey];
      setRule(ruleKey, { ...rule, conditions: [...rule.conditions, newCondition(meta)] });
    },
    [primitives, spec, setRule, markCustom],
  );

  const removeCondition = useCallback(
    (ruleKey: RuleKey, index: number) => {
      if (!spec) return;
      markCustom();
      const rule = spec[ruleKey];
      setRule(ruleKey, {
        ...rule,
        conditions: rule.conditions.filter((_, i) => i !== index),
      });
    },
    [spec, setRule, markCustom],
  );

  const changeCondition = useCallback(
    (ruleKey: RuleKey, index: number, patch: Partial<BacktestCondition>) => {
      if (!spec) return;
      markCustom();
      const rule = spec[ruleKey];
      const conditions = rule.conditions.map((cond, i) => (i === index ? { ...cond, ...patch } : cond));
      setRule(ruleKey, { ...rule, conditions });
    },
    [spec, setRule, markCustom],
  );

  const toggleConditionEnabled = useCallback(
    (ruleKey: RuleKey, index: number) => {
      if (!spec) return;
      markCustom();
      const rule = spec[ruleKey];
      const conditions = rule.conditions.map((cond, i) =>
        i === index ? { ...cond, enabled: cond.enabled === false } : cond,
      );
      setRule(ruleKey, { ...rule, conditions });
    },
    [spec, setRule, markCustom],
  );

  const changePrimitive = useCallback(
    (ruleKey: RuleKey, index: number, primitiveId: string) => {
      if (!spec) return;
      markCustom();
      const meta = primitiveById.get(primitiveId);
      if (!meta) return;
      const rule = spec[ruleKey];
      const conditions = rule.conditions.map((cond, i) =>
        i === index ? { primitive: primitiveId, trigger: "state" as const, params: {} } : cond,
      );
      // Resolve the new primitive's default params.
      const params: Record<string, number> = {};
      for (const p of meta.params) params[p.key] = p.default;
      setRule(ruleKey, {
        ...rule,
        conditions: conditions.map((cond, i) =>
          i === index ? { ...cond, params: { ...params } } : cond,
        ),
      });
    },
    [spec, primitiveById, setRule, markCustom],
  );

  const setParam = useCallback(
    (ruleKey: RuleKey, index: number, key: string, value: number) => {
      if (!spec) return;
      markCustom();
      const rule = spec[ruleKey];
      const conditions = rule.conditions.map((cond, i) =>
        i === index ? { ...cond, params: { ...cond.params, [key]: value } } : cond,
      );
      setRule(ruleKey, { ...rule, conditions });
    },
    [spec, setRule, markCustom],
  );

  const applyPreset = useCallback(
    (preset: BacktestPreset) => {
      setPresetId(preset.id);
      setLabel(preset.label);
      setSpec(cloneSpec(preset.spec));
    },
    [],
  );

  const runBacktest = useCallback(() => {
    if (!code || !spec) return;
    const seq = ++requestSeq.current;
    setLoading(true);
    setReport(null);
    const baseSpec = cloneSpec(spec);
    const payloadSpec: BacktestSpec = {
      buy: serializeRule(baseSpec.buy),
      sell: serializeRule(baseSpec.sell),
      allow_multiple_buys: multiBuys,
    };
    if (sellDisabled) payloadSpec.sell = { ...payloadSpec.sell, conditions: [] };
    const takeProfit = Number.parseFloat(takeProfitPct);
    const stopLoss = Number.parseFloat(stopLossPct);
    if (Number.isFinite(takeProfit) && takeProfit > 0) {
      payloadSpec.take_profit_pct = Math.round((takeProfit / 100) * 1e4) / 1e4;
    }
    if (Number.isFinite(stopLoss) && stopLoss > 0) {
      payloadSpec.stop_loss_pct = Math.round((stopLoss / 100) * 1e4) / 1e4;
    }
    api
      .getStockTrackerBacktest(code, { spec: payloadSpec, label, start, end })
      .then((res) => {
        if (requestSeq.current !== seq) return;
        setReport(res.backtest);
        onBacktestResult?.({ code, trades: res.backtest.trades ?? [] });
      })
      .catch(() => {
        if (requestSeq.current !== seq) return;
        setReport(null);
        onBacktestResult?.(null);
      })
      .finally(() => {
        if (requestSeq.current === seq) setLoading(false);
      });
  }, [code, spec, label, start, end, takeProfitPct, stopLossPct, sellDisabled, multiBuys, onBacktestResult]);

  // Reset the result when the selected symbol changes, then auto-run the
  // default preset once per symbol so the card is never an empty shell.
  useEffect(() => {
    requestSeq.current += 1;
    setReport(null);
    autoRanCode.current = null;
  }, [code]);

  useEffect(() => {
    if (!code || !spec) return;
    if (autoRanCode.current === code) return;
    autoRanCode.current = code;
    runBacktest();
  }, [code, spec, runBacktest]);

  if (!symbol) return null;

  const metrics: MetricDef[] = report
    ? [
        {
          labelKey: "stockTracker.backtestMetricTotal",
          value: signedPct(report.total_return),
          tone: (report.total_return ?? 0) >= 0 ? "up" : "down",
        },
        {
          labelKey: "stockTracker.backtestMetricAnnual",
          value: signedPct(report.annual_return),
          tone: (report.annual_return ?? 0) >= 0 ? "up" : "down",
        },
        {
          labelKey: "stockTracker.backtestMetricMaxDrawdown",
          value: plainPct(report.max_drawdown),
          tone: "down",
        },
        { labelKey: "stockTracker.backtestMetricSharpe", value: num(report.sharpe, 2) },
        { labelKey: "stockTracker.backtestMetricWinRate", value: plainPct(report.win_rate) },
        { labelKey: "stockTracker.backtestMetricProfitFactor", value: num(report.profit_factor, 2) },
        { labelKey: "stockTracker.backtestMetricTrades", value: String(report.trade_count) },
      ]
    : [];

  const canRun = Boolean(code && spec && spec.buy.conditions.length > 0);
  const headerMeta =
    report && !report.error
      ? `${report.label || label} · ${report.bars} ${t("stockTracker.backtestBars")}`
      : label || undefined;

  const runBtn = (
    <button
      type="button"
      onClick={runBacktest}
      disabled={!canRun || loading}
      className="inline-flex items-center gap-1 rounded-md border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
    >
      {loading ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <Play className="h-3 w-3" />
      )}
      {loading ? t("stockTracker.backtestRunning") : t("stockTracker.backtestRun")}
    </button>
  );

  // Embedded (bare) mode: collapsing the backtest section keeps just its title.
  if (bare && collapsed) {
    return (
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{t("stockTracker.backtestTitle")}</span>
        {headerMeta ? (
          <span className="text-[10px] text-muted-foreground">{headerMeta}</span>
        ) : null}
        <button
          type="button"
          onClick={toggle}
          aria-label={t("stockTracker.expand")}
          title={t("stockTracker.expand")}
          className="rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <ChevronDown className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div className={bare ? "" : "rounded-xl border border-border/60 bg-card p-4 shadow-sm"}>
      {!bare && (
        <ChartCardHeader
          title={t("stockTracker.backtestTitle")}
          helpText={t("stockTracker.backtestExplanation")}
          onHide={onHide}
          collapsed={collapsed}
          onToggle={toggle}
          meta={headerMeta}
          actions={runBtn}
        />
      )}

      {!bare && collapsed ? null : (
        <div className="flex flex-col gap-3">
          {bare ? (
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold">{t("stockTracker.backtestTitle")}</span>
              <span className="flex items-center gap-2">
                <span className="text-[10px] text-muted-foreground">{headerMeta}</span>
                {runBtn}
                <button
                  type="button"
                  onClick={toggle}
                  aria-label={t("stockTracker.collapse")}
                  title={t("stockTracker.collapse")}
                  className="rounded-md p-0.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                >
                  <ChevronDown className="h-4 w-4" />
                </button>
              </span>
            </div>
          ) : null}
          {/* Presets + date range */}
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
              {t("stockTracker.backtestPreset")}
              <select
                value={presetId}
                onChange={(e) => {
                  const preset = presets.find((p) => p.id === e.target.value);
                  if (preset) applyPreset(preset);
                }}
                className="rounded-md border border-border/60 bg-background px-2 py-1 text-xs outline-none focus:border-primary"
              >
                {presetId === "" ? (
                  <option value="">{t("stockTracker.backtestCustom")}</option>
                ) : null}
                {presets.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
              {t("stockTracker.backtestStart")}
              <input
                type="date"
                value={start}
                max={end}
                onChange={(e) => setStart(e.target.value)}
                className="rounded-md border border-border/60 bg-background px-2 py-1 text-xs outline-none focus:border-primary"
              />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
              {t("stockTracker.backtestEnd")}
              <input
                type="date"
                value={end}
                min={start}
                onChange={(e) => setEnd(e.target.value)}
                className="rounded-md border border-border/60 bg-background px-2 py-1 text-xs outline-none focus:border-primary"
              />
            </label>
          </div>

          {/* Optional exits: take-profit / stop-loss + disable-sell (long hold) */}
          <div className="flex flex-wrap items-end gap-x-3 gap-y-2 border-t border-border/40 pt-2">
            <span className="pb-1.5 text-[11px] font-medium text-muted-foreground">
              {t("stockTracker.backtestExitSettings")}
            </span>
            <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
              {t("stockTracker.backtestTakeProfit")}
              <input
                type="number"
                value={takeProfitPct}
                min={0}
                max={100}
                step={0.5}
                placeholder="—"
                onChange={(e) => setTakeProfitPct(e.target.value)}
                className="w-16 rounded-md border border-border/60 bg-background px-2 py-1 text-xs tabular-nums outline-none focus:border-primary"
              />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
              {t("stockTracker.backtestStopLoss")}
              <input
                type="number"
                value={stopLossPct}
                min={0}
                max={100}
                step={0.5}
                placeholder="—"
                onChange={(e) => setStopLossPct(e.target.value)}
                className="w-16 rounded-md border border-border/60 bg-background px-2 py-1 text-xs tabular-nums outline-none focus:border-primary"
              />
            </label>
            <label className="inline-flex items-center gap-1.5 pb-2 text-[11px] text-muted-foreground">
              <input
                type="checkbox"
                checked={multiBuys}
                onChange={(e) => {
                  markCustom();
                  setMultiBuys(e.target.checked);
                }}
                className="h-3.5 w-3.5 accent-primary"
              />
              {t("stockTracker.backtestMultiBuys")}
            </label>
            <label className="inline-flex items-center gap-1.5 pb-2 text-[11px] text-muted-foreground">
              <input
                type="checkbox"
                checked={sellDisabled}
                onChange={(e) => {
                  markCustom();
                  setSellDisabled(e.target.checked);
                }}
                className="h-3.5 w-3.5 accent-primary"
              />
              {t("stockTracker.backtestSellDisabled")}
            </label>
          </div>

          {/* Rule builder: buy / sell */}
          {spec ? (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {(["buy", "sell"] as const).map((ruleKey) => {
                const rule = spec[ruleKey];
                const isBuy = ruleKey === "buy";
                return (
                  <div
                    key={ruleKey}
                    className="flex flex-col gap-2 rounded-lg border border-border/40 p-2.5"
                  >
                    <div className="flex items-center justify-between">
                      <span
                        className={cn(
                          "text-xs font-semibold",
                          isBuy ? "text-success" : "text-danger",
                        )}
                      >
                        {isBuy
                          ? t("stockTracker.backtestBuyRule")
                          : t("stockTracker.backtestSellRule")}
                      </span>
                      <div className="flex items-center rounded-md border border-border/60 p-0.5">
                        {(["and", "or"] as const).map((mode) => (
                          <button
                            key={mode}
                            type="button"
                            aria-pressed={rule.mode === mode}
                            onClick={() => setMode(ruleKey, mode)}
                            className={cn(
                              "rounded px-2 py-0.5 text-[10px] transition",
                              rule.mode === mode
                                ? "bg-primary text-primary-foreground"
                                : "text-muted-foreground hover:text-foreground",
                            )}
                          >
                            {mode === "and"
                              ? t("stockTracker.backtestRuleAnd")
                              : t("stockTracker.backtestRuleOr")}
                          </button>
                        ))}
                      </div>
                    </div>

                    {ruleKey === "sell" && sellDisabled ? (
                      <p className="py-2 text-center text-[11px] text-muted-foreground/60">
                        {t("stockTracker.backtestSellDisabledNote")}
                      </p>
                    ) : (
                      <>
                        {rule.conditions.length === 0 ? (
                          <p className="py-2 text-center text-[11px] text-muted-foreground/60">
                            {t("stockTracker.backtestNoConditions")}
                          </p>
                        ) : (
                          rule.conditions.map((cond, index) => {
                            const pickable = isBuy ? primitives.filter((p) => !p.sell_only) : primitives;
                            const meta = primitiveById.get(cond.primitive);
                            const options = meta ?? pickable[0];
                            return (
                              <div
                                key={index}
                                className={cn(
                                  "flex flex-col gap-1.5 rounded-md bg-muted/30 px-2 py-1.5",
                                  cond.enabled === false && "opacity-45",
                                )}
                              >
                                <div className="flex flex-wrap items-center gap-1.5">
                                  <button
                                    type="button"
                                    aria-pressed={cond.enabled !== false}
                                    aria-label={cond.enabled === false ? "启用该条件" : "停用该条件"}
                                    title={cond.enabled === false ? "启用该条件（参与回测）" : "停用该条件（回测时忽略）"}
                                    onClick={() => toggleConditionEnabled(ruleKey, index)}
                                    className="rounded-md p-0.5 text-muted-foreground transition hover:text-foreground"
                                  >
                                    {cond.enabled === false ? (
                                      <Circle className="h-3.5 w-3.5" />
                                    ) : (
                                      <CircleDot className="h-3.5 w-3.5 text-primary" />
                                    )}
                                  </button>
                                  <PrimitiveMenu
                                    value={meta ? cond.primitive : ""}
                                    primitives={pickable}
                                    categories={categories}
                                    disabled={cond.enabled === false}
                                    onSelect={(primitiveId) => changePrimitive(ruleKey, index, primitiveId)}
                                  />
                                  <select
                                    value={cond.trigger}
                                    onChange={(e) =>
                                      changeCondition(ruleKey, index, {
                                        trigger: e.target.value as BacktestCondition["trigger"],
                                      })
                                    }
                                    disabled={cond.enabled === false}
                                    className="rounded-md border border-border/60 bg-background px-2 py-1 text-[11px] outline-none focus:border-primary disabled:cursor-not-allowed"
                                  >
                                    {options?.triggers.map((trig) => (
                                      <option key={trig.id} value={trig.id}>
                                        {trig.label}
                                      </option>
                                    ))}
                                  </select>
                                  <button
                                    type="button"
                                    onClick={() => removeCondition(ruleKey, index)}
                                    aria-label={t("stockTracker.backtestRemoveCondition")}
                                    title={t("stockTracker.backtestRemoveCondition")}
                                    className="rounded-md p-1 text-muted-foreground transition hover:bg-danger/10 hover:text-danger"
                                  >
                                    <X className="h-3.5 w-3.5" />
                                  </button>
                                </div>
                                {meta && meta.params.length > 0 ? (
                                  <div className="flex flex-wrap items-center gap-1.5 pl-6">
                                    {meta.params.map((param) => (
                                      <label
                                        key={param.key}
                                        className="flex items-center gap-1 text-[10px] text-muted-foreground"
                                      >
                                        {param.label}
                                        <input
                                          type="number"
                                          value={cond.params[param.key] ?? param.default}
                                          min={param.min}
                                          max={param.max}
                                          step={param.default % 1 === 0 ? 1 : 0.5}
                                          disabled={cond.enabled === false}
                                          onChange={(e) =>
                                            setParam(ruleKey, index, param.key, Number(e.target.value))
                                          }
                                          className="w-16 rounded-md border border-border/60 bg-background px-1.5 py-0.5 text-[11px] tabular-nums outline-none focus:border-primary disabled:cursor-not-allowed"
                                        />
                                      </label>
                                    ))}
                                  </div>
                                ) : null}
                              </div>
                            );
                          })
                        )}

                        <button
                          type="button"
                          onClick={() => addCondition(ruleKey)}
                          className="inline-flex items-center justify-center gap-1 rounded-md border border-dashed border-border/70 px-2 py-1 text-[11px] text-muted-foreground transition hover:bg-muted hover:text-foreground"
                        >
                          <Plus className="h-3 w-3" />
                          {t("stockTracker.backtestAddCondition")}
                        </button>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="flex h-[120px] items-center justify-center text-xs text-muted-foreground">
              {t("stockTracker.backtestLoading")}
            </div>
          )}

          {/* Results */}
          {!report && !loading ? (
            <div className="flex h-[200px] flex-col items-center justify-center gap-2 text-muted-foreground">
              <LineChart className="h-8 w-8 opacity-40" />
              <span className="text-xs">{t("stockTracker.backtestEmpty")}</span>
            </div>
          ) : report?.error ? (
            <div className="flex h-[200px] flex-col items-center justify-center gap-2 text-muted-foreground">
              <LineChart className="h-8 w-8 opacity-40" />
              <span className="text-xs text-danger">{report.error}</span>
            </div>
          ) : report ? (
            <>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
                {metrics.map((metric) => (
                  <div
                    key={metric.labelKey}
                    className="rounded-lg border border-border/40 bg-muted/30 px-2 py-1.5"
                  >
                    <p className="text-[10px] text-muted-foreground">{t(metric.labelKey as never)}</p>
                    <p
                      className={cn(
                        "font-mono text-sm font-semibold tabular-nums",
                        metric.tone === "up" && "text-success",
                        metric.tone === "down" && "text-danger",
                        (!metric.tone || metric.tone === "plain") && "text-foreground",
                      )}
                    >
                      {metric.value}
                    </p>
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
