import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { X, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { getSignalLabelKey, normalizeAShareCode } from "@/lib/stockTracker";
import type { SignalType, TrackerConfig } from "@/lib/api";

interface TrackerConfigPanelProps {
  config: TrackerConfig;
  onSave: (config: TrackerConfig) => void;
  disabled?: boolean;
}

const ALL_SIGNALS: SignalType[] = ["volume_spike", "breakout", "ma_alignment"];
const PERIOD_PRESETS = [5, 10, 20, 60];

export function TrackerConfigPanel({ config, onSave, disabled }: TrackerConfigPanelProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<TrackerConfig>(config);
  const [watchlistInput, setWatchlistInput] = useState(config.watchlist.join(", "));

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

  const toggleSignal = (signal: SignalType) => {
    setDraft((prev) => {
      const next = prev.signals.includes(signal)
        ? prev.signals.filter((s) => s !== signal)
        : [...prev.signals, signal];
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

  const updateThreshold = (key: keyof TrackerConfig["thresholds"], value: string) => {
    const num = parseFloat(value);
    if (Number.isNaN(num)) return;
    setDraft((prev) => ({
      ...prev,
      thresholds: { ...prev.thresholds, [key]: num },
    }));
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
                {ALL_SIGNALS.map((signal) => (
                  <button
                    key={signal}
                    type="button"
                    onClick={() => toggleSignal(signal)}
                    className={cn(
                      "rounded-full px-2.5 py-1 text-xs transition",
                      draft.signals.includes(signal)
                        ? "bg-primary text-primary-foreground"
                        : "border bg-background text-muted-foreground hover:bg-muted",
                    )}
                  >
                    {t(getSignalLabelKey(signal))}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">{t("stockTracker.thresholds")}</label>
              <div className="grid grid-cols-2 gap-2">
                <ThresholdInput
                  label={t("stockTracker.volumeSpike")}
                  value={draft.thresholds.volume_spike}
                  onChange={(v) => updateThreshold("volume_spike", v)}
                />
                <ThresholdInput
                  label={t("stockTracker.breakout")}
                  value={draft.thresholds.breakout_window}
                  onChange={(v) => updateThreshold("breakout_window", v)}
                />
              </div>
            </div>
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
