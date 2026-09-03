import { CalendarClock } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  formatDataDate,
  formatEventDate,
  getEventRiskChipClass,
  getEventRiskScoreTone,
  getEventRiskToneClass,
} from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { EventItem, SymbolSnapshot } from "@/lib/api";

interface EventTimelineCardProps {
  symbol: SymbolSnapshot | null;
  onHide?: () => void;
}

const EVENT_TYPE_LABEL_KEY: Record<string, string> = {
  lockup: "stockTracker.eventLockup",
  earnings_forecast: "stockTracker.eventForecast",
  dragon_tiger: "stockTracker.eventDragonTiger",
  holder_trade: "stockTracker.eventHolderTrade",
};

/**
 * Timeline of a symbol's upcoming / recent corporate events (future-90-day
 * lockup unlocks, earnings forecasts, dragon-tiger appearances, shareholder
 * trades) plus the composite event-risk score. High-risk entries render red.
 */
export function EventTimelineCard({ symbol, onHide }: EventTimelineCardProps) {
  const { t } = useTranslation();
  const events = symbol?.events;
  const items = events?.items ?? [];
  const score = events?.event_risk_score ?? null;
  const hasItems = items.length > 0;
  const source = events?.source;

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.eventTitle")}
        helpText={t("stockTracker.eventExplanation")}
        onHide={onHide}
        meta={t("stockTracker.dataDate", {
          date: formatDataDate(events?.as_of ?? symbol?.valuation?.trade_date),
        })}
      />
      {!hasItems ? (
        <div className="flex h-[240px] flex-col items-center justify-center gap-2 text-muted-foreground">
          <CalendarClock className="h-8 w-8 opacity-40" />
          <span className="text-xs">{t("stockTracker.noEventData")}</span>
          {events?.error ? (
            <span className="max-w-[220px] truncate text-[10px] text-muted-foreground/70">
              {events.error}
            </span>
          ) : null}
        </div>
      ) : (
        <>
          <div className="mb-3 flex items-center justify-between">
            <span className="text-[11px] text-muted-foreground">
              {t("stockTracker.eventTimelineLabel")}
            </span>
            <div className="flex items-center gap-1.5">
              {source ? (
                <span className="rounded bg-muted/40 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {t("stockTracker.dataSource", { source })}
                </span>
              ) : null}
              {score != null ? (
                <span
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full bg-muted/40 px-2 py-0.5 text-xs font-semibold tabular-nums",
                    getEventRiskScoreTone(score),
                  )}
                  title={t("stockTracker.eventRiskScoreHint")}
                >
                  {t("stockTracker.eventRiskScore")} {score.toFixed(0)}
                </span>
              ) : null}
            </div>
          </div>
          <ul className="flex max-h-[220px] flex-col gap-1.5 overflow-y-auto pr-1">
            {items.map((item, index) => (
              <EventRow key={`${item.event_date ?? index}-${index}`} item={item} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function EventRow({ item }: { item: EventItem }) {
  const { t } = useTranslation();
  const labelKey = EVENT_TYPE_LABEL_KEY[item.event_type];
  const isDanger = item.risk_level === "danger";

  return (
    <li
      className={cn(
        "flex flex-col gap-0.5 rounded-lg px-2 py-1.5",
        isDanger ? "bg-danger/5" : "bg-muted/40",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[9px] font-medium uppercase leading-none",
              getEventRiskChipClass(item.risk_level),
            )}
          >
            {labelKey ? t(labelKey as never) : item.event_type}
          </span>
          <span
            className={cn(
              "truncate text-xs font-medium",
              getEventRiskToneClass(item.risk_level),
            )}
          >
            {item.title}
          </span>
        </div>
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
          {formatEventDate(item.event_date, item.days_until)}
        </span>
      </div>
      {item.summary ? (
        <p className="truncate pl-[calc(1.5rem+8px)] text-[10px] text-muted-foreground/80">
          {item.summary}
        </p>
      ) : null}
    </li>
  );
}
