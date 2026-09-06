import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Bell, CheckCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import type { StockTrackerAlert } from "@/lib/api";

interface AlertBellProps {
  alerts: StockTrackerAlert[];
  unreadCount: number;
  onAckAll: () => void;
  onSelect: (code: string) => void;
}

export function AlertBell({ alerts, unreadCount, onAckAll, onSelect }: AlertBellProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative inline-flex items-center gap-1.5 rounded-md border border-border/60 bg-background px-2.5 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground"
        title={t("stockTracker.alerts")}
      >
        <Bell className="h-4 w-4" />
        {unreadCount > 0 && (
          <span className="absolute -right-1.5 -top-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-semibold text-white">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-96 rounded-xl border border-border/60 bg-card shadow-lg">
          <div className="flex items-center justify-between border-b border-border/60 px-4 py-3">
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              {t("stockTracker.alerts")}
              {unreadCount > 0 && (
                <span className="rounded-full bg-danger/10 px-2 py-0.5 text-xs font-medium text-danger">
                  {unreadCount}
                </span>
              )}
            </h3>
            {unreadCount > 0 && (
              <button
                type="button"
                onClick={onAckAll}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground"
              >
                <CheckCheck className="h-3.5 w-3.5" />
                {t("stockTracker.markAllRead")}
              </button>
            )}
          </div>

          <div className="max-h-80 overflow-y-auto">
            {alerts.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                {t("stockTracker.noAlerts")}
              </div>
            ) : (
              <ul className="divide-y divide-border/60">
                {alerts.map((alert) => (
                  <li key={alert.id}>
                    <button
                      type="button"
                      onClick={() => onSelect(alert.code)}
                      className="flex w-full items-start gap-2.5 px-4 py-2.5 text-left transition hover:bg-muted/50"
                    >
                      <span
                        className={cn(
                          "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                          alert.acknowledged ? "bg-muted-foreground/30" : "bg-primary",
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium">
                            {alert.name || alert.code}
                          </span>
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {alert.strategy_label}
                          </span>
                        </span>
                        <span className="mt-0.5 block text-xs text-muted-foreground">
                          {alert.code}
                          {alert.price != null && (
                            <> · ¥{alert.price.toFixed(2)}</>
                          )}{" "}
                          · {alert.signal_date}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
