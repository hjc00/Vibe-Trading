import { useMemo, useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { X, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { getSignalLabelKey, normalizeAShareCode } from "@/lib/stockTracker";
import type { SignalMeta, TrackerConfig } from "@/lib/api";

interface TrackerConfigPanelProps {
  config: TrackerConfig;
  onSave: (config: TrackerConfig) => void;
  disabled?: boolean;
  signalMeta?: SignalMeta[];
}

const PERIOD_PRESETS = [5, 10, 20, 60];

const FALLBACK_SIGNALS: SignalMeta[] = [
  {
    name: "volume_spike",
    category: "volume",
    direction: "neutral",
    label: "Volume spike",
    description: "Latest volume is unusually large versus the recent average.",
    params: {
      volume_spike: { type: "float", min: 1, default: 2, description: "Volume ratio versus recent average." },
    },
    default_params: { volume_spike: 2 },
    format: "multiple",
    ranking_enabled: true,
    show_in_table: true,
    is_global: false,
  },
  {
    name: "breakout",
    category: "momentum",
    direction: "both",
    label: "Breakout",
    description: "Price closes above the recent high or below the recent low.",
    params: {
      breakout_window: { type: "int", min: 5, max: 250, default: 20, description: "Days used to define the recent range." },
    },
    default_params: { breakout_window: 20 },
    format: "percent",
    ranking_enabled: true,
    show_in_table: true,
    is_global: false,
  },
  {
    name: "ma_alignment",
    category: "trend",
    direction: "both",
    label: "MA alignment",
    description: "Moving averages are aligned bullishly or bearishly.",
    params: {},
    default_params: {},
    format: "percent",
    ranking_enabled: false,
    show_in_table: false,
    is_global: true,
  },
  {
    name: "rsi",
    category: "momentum",
    direction: "both",
    label: "RSI",
    description: "RSI reaches overbought or oversold levels.",
    params: {
      rsi_overbought: { type: "float", min: 50, max: 100, default: 70, description: "RSI level considered overbought." },
      rsi_oversold: { type: "float", min: 0, max: 50, default: 30, description: "RSI level considered oversold." },
    },
    default_params: { rsi_overbought: 70, rsi_oversold: 30 },
    format: "raw",
    ranking_enabled: true,
    show_in_table: true,
    is_global: false,
  },
];

export function TrackerConfigPanel({ config, onSave, disabled, signalMeta }: TrackerConfigPanelProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<TrackerConfig>(config);
  const [watchlistInput, setWatchlistInput] = useState(config.watchlist.join(", "));

  const signals = signalMeta ?? FALLBACK_SIGNALS;
  const allParams = useMemo(() => {
    const params: { key: string; meta: SignalMeta; param: SignalMeta["params"][string] }[] = [];
    for (const meta of signals) {
      if (!draft.signals.includes(meta.name)) continue;
      for (const [key, param] of Object.entries(meta.params)) {
        params.push({ key, meta, param });
      }
    }
    return params;
  }, [signals, draft.signals]);

  useEffect(() => {
    if (open) {
      setDraft(config);
      setWatchlistInput(config.watchlist.join(", "));
    }
  }, [open, config]);

  const handleSave = () => {
    const normalizedCodes = watchlistInput
      .split(/[,，\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean)
      .map(normalizeAShareCode)
      .filter((c): c is string => c !== null);

    const nextConfig: TrackerConfig = {
      ...draft,
      watchlist: normalizedCodes.length > 0 ? normalizedCodes : config.watchlist,
    };
    onSave(nextConfig);
    setOpen(false);
  };

  const toggleSignal = (signalName: string) => {
    setDraft((prev) => {
      const next = prev.signals.includes(signalName)
        ? prev.signals.filter((s) => s !== signalName)
        : [...prev.signals, signalName];
      return { ...prev, signals: next };
    });
  };

  const togglePeriod = (period: number) => {
    setDraft((prev) => {
      const next = prev.periods.includes(period)
        ? prev.periods.filter((p) => p !== period)
        : [...prev.periods, period].sort((a, b) => a - b);
      return { ...prev, periods: next };
    });
  };

  const updateThreshold = (key: string, value: string) => {
    const num = parseFloat(value);
    if (Number.isNaN(num)) return;
    setDraft((prev) => ({
      ...prev,
      thresholds: { ...prev.thresholds, [key]: num },
    }));
  };

  const updateRefreshInterval = (value: string) => {
    const num = parseInt(value, 10);
    if (Number.isNaN(num)) return;
    setDraft((prev) => ({ ...prev, refresh_interval_seconds: Math.max(5, num) }));
  };

  const saveDisabled =
    watchlistInput.trim().length === 0 ||
    draft.signals.length === 0 ||
    draft.periods.length === 0;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-60"
      >
        <Settings2 className="h-4 w-4" />
        {t("stockTracker.settings")}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 rounded-xl border border-border/60 bg-card p-4 shadow-lg">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-semibold">{t("stockTracker.editConfig")}</h3>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">{t("stockTracker.watchlist")}</label>
              <textarea
                value={watchlistInput}
                onChange={(e) => setWatchlistInput(e.target.value)}
                className={cn(
                  "min-h-[60px] w-full rounded-md border bg-background px-3 py-2 text-xs outline-none",
                  "focus:border-primary focus:ring-2 focus:ring-primary/20",
                )}
                placeholder="000001.SZ, 600519.SH"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">{t("stockTracker.periods")}</label>
              <div className="flex flex-wrap gap-2">
                {PERIOD_PRESETS.map((period) => (
                  <button
                    key={period}
                    type="button"
                    onClick={() => togglePeriod(period)}
                    className={cn(
                      "rounded-full px-2.5 py-1 text-xs transition",
                      draft.periods.includes(period)
                        ? "bg-primary text-primary-foreground"
                        : "border bg-background text-muted-foreground hover:bg-muted",
                    )}
                  >
                    {period}
                    {t("stockTracker.period")}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">{t("stockTracker.signals")}</label>
              <div className="flex flex-wrap gap-2">
                {signals.map((signal) => (
                  <button
                    key={signal.name}
                    type="button"
                    onClick={() => toggleSignal(signal.name)}
                    className={cn(
                      "rounded-full px-2.5 py-1 text-xs transition",
                      draft.signals.includes(signal.name)
                        ? "bg-primary text-primary-foreground"
                        : "border bg-background text-muted-foreground hover:bg-muted",
                    )}
                    title={signal.description}
                  >
                    {t(getSignalLabelKey(signal.name) as never)}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground" htmlFor="refresh-interval">
                {t("stockTracker.refreshInterval")}
              </label>
              <input
                id="refresh-interval"
                type="number"
                min={5}
                value={draft.refresh_interval_seconds}
                onChange={(e) => updateRefreshInterval(e.target.value)}
                className={cn(
                  "w-full rounded-md border bg-background px-3 py-2 text-xs outline-none",
                  "focus:border-primary focus:ring-2 focus:ring-primary/20",
                )}
              />
              <p className="text-[10px] text-muted-foreground">{t("stockTracker.refreshIntervalHint")}</p>
            </div>

            {allParams.length > 0 && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">{t("stockTracker.thresholds")}</label>
                <div className="grid grid-cols-2 gap-2">
                  {allParams.map(({ key, param }) => (
                    <ThresholdInput
                      key={key}
                      label={t(getSignalLabelKey(key) as never)}
                      value={draft.thresholds[key] ?? param.default}
                      onChange={(v) => updateThreshold(key, v)}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setDraft(config);
                setWatchlistInput(config.watchlist.join(", "));
                setOpen(false);
              }}
              className="rounded-md border px-3 py-1.5 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground"
            >
              {t("stockTracker.cancel")}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saveDisabled}
              className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {t("stockTracker.save")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ThresholdInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-1">
      <label className="text-[10px] text-muted-foreground">{label}</label>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "w-full rounded-md border bg-background px-2 py-1 text-xs outline-none",
          "focus:border-primary focus:ring-2 focus:ring-primary/20",
        )}
      />
    </div>
  );
}
