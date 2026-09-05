import { Check, ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";
import type { BacktestPrimitiveCategory, BacktestPrimitiveMeta } from "@/lib/api";

/**
 * Cascading two-level picker for a backtest rule primitive.
 *
 * Level 1 shows the primitive categories (均线 / MACD / KDJ / …); hovering a
 * category flies out its concrete primitives to the side, and clicking one
 * selects it — the same two-level shape as a classic nested context menu. A
 * native <select> would flatten everything into one long list, which is what
 * this replaces.
 *
 * The popup is anchored to the trigger button; it flips upward when it would
 * otherwise run past the viewport bottom, and the flyout opens left when it
 * would overflow the right edge. Keyboard: ↑/↓ move across categories (or
 * within an open flyout), → opens the flyout, ← closes it, Enter selects,
 * Esc closes. With no category catalog (older backend) it degrades to a flat
 * single-level list.
 */

const CAT_ROW_H = 30; // rough px per category row, for the open-direction guess
const FLYOUT_W = 216; // rough px of the flyout, for the side flip guess
const EDGE_PAD = 8;

interface PrimitiveMenuProps {
  /** Selected primitive id ("" when unknown). */
  value: string;
  /** Selectable primitives for this rule (sell_only already filtered by caller). */
  primitives: BacktestPrimitiveMeta[];
  /** Ordered category catalog driving the level-1 list. */
  categories: BacktestPrimitiveCategory[];
  disabled?: boolean;
  onSelect: (primitiveId: string) => void;
  className?: string;
}

export function PrimitiveMenu({
  value,
  primitives,
  categories,
  disabled = false,
  onSelect,
  className,
}: PrimitiveMenuProps) {
  const [open, setOpen] = useState(false);
  const [openUp, setOpenUp] = useState(false);
  const [flyoutLeft, setFlyoutLeft] = useState(false);
  const [openCatId, setOpenCatId] = useState<string | null>(null);
  const [activeItem, setActiveItem] = useState(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Categories that still have selectable primitives after the caller's
  // sell_only filter (empty ones never appear in the level-1 list).
  const visibleCats = categories.filter((cat) =>
    primitives.some((p) => p.category === cat.id),
  );
  const current = primitives.find((p) => p.id === value) ?? null;
  const currentCatId = current?.category ?? null;
  // No category catalog (older backend): fall back to a flat single-level list.
  const flat = visibleCats.length === 0;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  // Drop-down direction + flyout side need viewport math only at open time.
  useEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const estH = 10 + visibleCats.length * CAT_ROW_H + 10;
    const fitsBelow = window.innerHeight - rect.bottom >= estH + EDGE_PAD;
    const fitsAbove = rect.top >= estH + EDGE_PAD;
    setOpenUp(!fitsBelow && fitsAbove);
    setFlyoutLeft(false);
    // Open on the category holding the current primitive, so the user sees
    // where their selection lives; fall back to the first populated category.
    const startCat = currentCatId ?? visibleCats[0]?.id ?? null;
    setOpenCatId(startCat);
    setActiveItem(-1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const openMenu = () => {
    if (disabled) return;
    setOpen(true);
  };

  const select = (id: string) => {
    onSelect(id);
    setOpen(false);
  };

  const itemsOf = (catId: string) =>
    primitives.filter((p) => p.category === catId);

  const reveal = (catId: string) => {
    if (!panelRef.current) {
      setOpenCatId(catId);
      setActiveItem(-1);
      return;
    }
    const m = panelRef.current.getBoundingClientRect();
    const fitsRight = m.right + FLYOUT_W + EDGE_PAD <= window.innerWidth;
    const fitsLeft = m.left - FLYOUT_W - EDGE_PAD >= 0;
    setFlyoutLeft(!fitsRight && fitsLeft);
    setOpenCatId(catId);
    setActiveItem(-1);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Escape") {
      if (openCatId) {
        setOpenCatId(null);
        setActiveItem(-1);
      } else {
        setOpen(false);
      }
      return;
    }
    if (!open) return;
    const idxOf = (id: string | null) => visibleCats.findIndex((c) => c.id === id);
    const openIdx = idxOf(openCatId);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      if (openCatId && openIdx >= 0) {
        // Move within the open flyout when one is showing.
        const items = itemsOf(openCatId);
        if (items.length === 0) return;
        setActiveItem((cur) => {
          const base = cur < 0 ? (step > 0 ? -1 : 0) : cur;
          return (base + step + items.length) % items.length;
        });
      } else if (visibleCats.length > 0) {
        const next = (Math.max(0, idxOf(openCatId)) + step + visibleCats.length) % visibleCats.length;
        reveal(visibleCats[next].id);
      }
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      const id = openCatId ?? visibleCats[Math.max(0, idxOf(currentCatId))]?.id;
      if (id) reveal(id);
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setOpenCatId(null);
      setActiveItem(-1);
      return;
    }
    if (event.key === "Enter" && openCatId) {
      event.preventDefault();
      const items = itemsOf(openCatId);
      const pick = activeItem >= 0 ? items[activeItem] : items[0];
      if (pick) select(pick.id);
    }
  };

  const label = current?.label ?? "—";

  return (
    <div ref={rootRef} className={cn("relative min-w-[150px] flex-1", className)}>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onKeyDown}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-1 rounded-md border border-border/60 bg-background px-2 py-1 text-[11px] outline-none transition focus:border-primary disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span className="min-w-0 truncate text-left">{label}</span>
        <ChevronDown
          className={cn("h-3 w-3 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")}
        />
      </button>

      {open && primitives.length > 0 ? (
        <div
          ref={panelRef}
          role="menu"
          onMouseLeave={() => {
            setOpenCatId(null);
            setActiveItem(-1);
          }}
          className={cn(
            "absolute z-50 min-w-full w-40 rounded-md border bg-card p-1 shadow-lg ring-1 ring-black/5",
            openUp ? "bottom-full mb-1" : "top-full mt-1",
          )}
        >
          {flat ? (
            primitives.map((p) => (
              <button
                key={p.id}
                type="button"
                role="menuitem"
                aria-selected={p.id === value}
                title={p.description}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => select(p.id)}
                className={cn(
                  "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[11px] leading-snug transition-colors hover:bg-muted/70",
                  p.id === value && "text-primary",
                )}
              >
                <span className="min-w-0 flex-1 whitespace-normal break-words">{p.label}</span>
                <Check className={cn("h-3 w-3 shrink-0", p.id === value ? "opacity-100" : "opacity-0")} />
              </button>
            ))
          ) : (
            visibleCats.map((cat) => {
              const items = itemsOf(cat.id);
              const isOpen = openCatId === cat.id;
              return (
                <div key={cat.id} className="relative">
                  <button
                    type="button"
                    role="menuitem"
                    aria-haspopup="menu"
                    aria-expanded={isOpen}
                    onMouseEnter={() => reveal(cat.id)}
                    onClick={() => reveal(cat.id)}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-[11px] transition-colors",
                      isOpen ? "bg-muted text-foreground" : "text-foreground hover:bg-muted/70",
                    )}
                  >
                    <span className="min-w-0 truncate">{cat.label}</span>
                    <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
                  </button>
                  {isOpen ? (
                    <div
                      role="menu"
                      className={cn(
                        "absolute top-0 z-10 w-max max-w-[14rem] rounded-md border bg-card p-1 shadow-lg ring-1 ring-black/5",
                        flyoutLeft ? "right-full mr-1" : "left-full ml-1",
                      )}
                    >
                      {items.map((p, i) => {
                        const selected = p.id === value;
                        return (
                          <button
                            key={p.id}
                            type="button"
                            role="menuitem"
                            aria-selected={selected}
                            title={p.description}
                            onMouseEnter={() => setActiveItem(i)}
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => select(p.id)}
                            className={cn(
                              "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[11px] leading-snug transition-colors",
                              activeItem === i
                                ? "bg-muted text-foreground"
                                : "text-foreground hover:bg-muted/70",
                              selected && "text-primary",
                            )}
                          >
                            <span className="min-w-0 flex-1 whitespace-normal break-words">{p.label}</span>
                            <Check className={cn("h-3 w-3 shrink-0", selected ? "opacity-100" : "opacity-0")} />
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      ) : null}
    </div>
  );
}
