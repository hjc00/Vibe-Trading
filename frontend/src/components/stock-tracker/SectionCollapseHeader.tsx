import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";

interface SectionCollapseHeaderProps {
  title: string;
  meta?: string | null;
  actions?: ReactNode;
  collapsed: boolean;
  onToggle: () => void;
}

/**
 * Collapsible section header for zones embedded in a shared card (bare mode).
 * Collapsed keeps a slim title row with an expand chevron; expanded renders
 * the meta / action cluster plus a collapse chevron on the right.
 */
export function SectionCollapseHeader({
  title,
  meta,
  actions,
  collapsed,
  onToggle,
}: SectionCollapseHeaderProps) {
  const { t } = useTranslation();
  const metaEl = meta ? (
    <span className="text-[10px] text-muted-foreground">{meta}</span>
  ) : null;
  if (collapsed) {
    return (
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{title}</span>
        {metaEl}
        <button
          type="button"
          onClick={onToggle}
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
    <div className="flex items-center justify-between gap-2">
      <span className="text-sm font-semibold">{title}</span>
      <span className="flex items-center gap-2">
        {metaEl}
        {actions}
        <button
          type="button"
          onClick={onToggle}
          aria-label={t("stockTracker.collapse")}
          title={t("stockTracker.collapse")}
          className="rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <ChevronDown className="h-4 w-4" />
        </button>
      </span>
    </div>
  );
}
