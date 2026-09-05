import { useTranslation } from "react-i18next";
import { ArrowUp, ListOrdered } from "lucide-react";
import { cn } from "@/lib/utils";

export interface NavSection {
  id: string;
  labelKey: string;
}

interface SectionNavProps {
  sections: NavSection[];
  activeId: string | null;
  onNavigate: (id: string) => void;
  onBackToTop: () => void;
}

/**
 * Right-hand sticky outline for the stock-tracker dashboard. Lists the page's
 * logical regions as anchor links; the section currently in view is highlighted
 * by the caller via `activeId` (from `useSectionSpy`). A "back to top" action
 * rides at the bottom. Rendered only on wide (`xl`) viewports — on smaller
 * screens the floating BackToTopButton takes over.
 */
export function SectionNav({ sections, activeId, onNavigate, onBackToTop }: SectionNavProps) {
  const { t } = useTranslation();
  if (sections.length === 0) return null;

  return (
    <aside
      aria-label={t("stockTracker.navTitle")}
      className="sticky top-6 hidden h-fit w-44 shrink-0 self-start rounded-xl border border-border/60 bg-card/80 p-2 shadow-sm backdrop-blur xl:block"
    >
      <div className="flex items-center gap-1.5 px-2 py-1.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        <ListOrdered className="h-3 w-3" />
        {t("stockTracker.navTitle")}
      </div>
      <nav className="flex flex-col gap-0.5">
        {sections.map(({ id, labelKey }) => {
          const isActive = activeId === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => onNavigate(id)}
              aria-current={isActive ? "true" : undefined}
              className={cn(
                "group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition",
                isActive
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <span
                className={cn(
                  "h-1.5 w-1.5 shrink-0 rounded-full transition",
                  isActive
                    ? "bg-primary"
                    : "bg-border group-hover:bg-muted-foreground/50",
                )}
              />
              {t(labelKey as never)}
            </button>
          );
        })}
      </nav>
      <button
        type="button"
        onClick={onBackToTop}
        className="mt-1.5 flex w-full items-center gap-2 rounded-md border-t border-border/60 px-2 pb-1 pt-2 text-left text-xs text-muted-foreground transition hover:text-foreground"
      >
        <ArrowUp className="h-3.5 w-3.5 shrink-0" />
        {t("stockTracker.navBackToTop")}
      </button>
    </aside>
  );
}
