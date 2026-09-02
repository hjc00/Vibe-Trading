import { ChevronDown, CircleHelp } from "lucide-react";
import { cn } from "@/lib/utils";

interface ChartCardHeaderProps {
  title: string;
  helpText: string;
  meta?: string;
  collapsed?: boolean;
  onToggle?: () => void;
}

export function ChartCardHeader({
  title,
  helpText,
  meta,
  collapsed = false,
  onToggle,
}: ChartCardHeaderProps) {
  const togglable = onToggle != null;
  return (
    <div
      className={cn(
        "group relative flex items-center justify-between",
        togglable ? "mb-1" : "mb-2",
      )}
    >
      <h3 className="text-sm font-semibold">{title}</h3>
      <div className="flex items-center gap-1">
        {meta ? <span className="text-[10px] text-muted-foreground">{meta}</span> : null}
        <button
          type="button"
          className="inline-flex items-center justify-center rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          aria-label={helpText}
        >
          <CircleHelp className="h-4 w-4" />
        </button>
        {onToggle ? (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={!collapsed}
            aria-label={title}
            className="inline-flex items-center justify-center rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            <ChevronDown
              className={cn("h-4 w-4 transition-transform", collapsed && "rotate-180")}
            />
          </button>
        ) : null}
        <div
          role="tooltip"
          className="invisible absolute right-2 top-full z-50 mt-2 w-72 rounded-md border border-border/60 bg-popover p-3 text-xs leading-relaxed text-popover-foreground opacity-0 shadow-lg ring-1 ring-black/5 transition-opacity duration-150 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
        >
          {helpText}
        </div>
      </div>
    </div>
  );
}
