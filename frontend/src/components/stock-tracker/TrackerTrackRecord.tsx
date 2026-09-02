import { useTranslation } from "react-i18next";
import type { TrackerTrackRecordItem } from "@/lib/api";
import {
  formatPriceZoneText,
  formatTrackPrice,
  getActionLabelKey,
  getActionToneClass,
  getStatusLabelKey,
  getStatusToneClass,
} from "@/lib/stockTracker";

interface TrackerTrackRecordProps {
  items: TrackerTrackRecordItem[];
}

export function TrackerTrackRecord({ items }: TrackerTrackRecordProps) {
  const { t } = useTranslation();
  if (!items || items.length === 0) return null;

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold">{t("stockTracker.trackRecordTitle")}</h3>
      <div className="space-y-2">
        {items.map((item) => (
          <TrackRecordRow key={item.analysis_id + item.code} item={item} />
        ))}
      </div>
    </div>
  );
}

function TrackRecordRow({ item }: { item: TrackerTrackRecordItem }) {
  const { t } = useTranslation();
  const fields: { label: string; value: string; tone?: string }[] = [];
  const entry = formatPriceZoneText(item.entry_zone);
  const target = formatPriceZoneText(item.target_zone);
  if (entry) fields.push({ label: t("stockTracker.entryZone"), value: entry, tone: "text-success" });
  if (target) fields.push({ label: t("stockTracker.targetZone"), value: target });
  if (item.stop_loss != null) {
    fields.push({ label: t("stockTracker.stopLoss"), value: formatTrackPrice(item.stop_loss), tone: "text-danger" });
  }
  if (item.current_close != null) {
    fields.push({ label: t("stockTracker.currentPrice"), value: formatTrackPrice(item.current_close), tone: "text-foreground" });
  }

  return (
    <div className="rounded-lg border border-border/40 bg-muted/20 p-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <div className="flex flex-col">
          <span className="text-xs font-semibold">{item.name ?? item.code}</span>
          <span className="font-mono text-[10px] text-muted-foreground">
            {item.code} · {item.analysis_id.slice(0, 15)}
          </span>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${getActionToneClass(item.action)}`}>
          {t(getActionLabelKey(item.action))}
        </span>
        <span className={`ml-auto rounded-full px-2 py-0.5 text-[11px] font-medium ${getStatusToneClass(item.status)}`}>
          {t(getStatusLabelKey(item.status))}
        </span>
      </div>
      {fields.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
          {fields.map((field, index) => (
            <span key={index} className="text-muted-foreground">
              {field.label}:{" "}
              <span className={`font-mono font-medium ${field.tone ?? "text-foreground/90"}`}>
                {field.value}
              </span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
