import { useCallback, useState } from "react";
import { safeGet, safeSet } from "@/lib/storage";

const CARD_COLLAPSE_KEY_PREFIX = "stockTracker.cardCollapsed";

/** localStorage key holding a dashboard card's collapsed state ("1"/"0"). */
export function cardCollapseKey(id: string): string {
  return `${CARD_COLLAPSE_KEY_PREFIX}.${id}`;
}

/** Read a collapse preference under a storage key (defaults to expanded). */
export function isCardCollapsed(storageKey: string): boolean {
  return safeGet(storageKey) === "1";
}

/** Persist a collapse preference under a storage key. */
export function setCardCollapsed(storageKey: string, collapsed: boolean): void {
  safeSet(storageKey, collapsed ? "1" : "0");
}

/**
 * Persisted open/collapsed state for a dashboard card. Defaults to expanded.
 * `storageKey` lets callers reuse a pre-existing key (e.g. SectorStrengthBoard);
 * otherwise the id-derived key is used.
 */
export function useCardCollapse(
  id: string,
  storageKey: string = cardCollapseKey(id),
): { collapsed: boolean; toggle: () => void } {
  const [collapsed, setCollapsed] = useState<boolean>(() =>
    isCardCollapsed(storageKey),
  );
  const toggle = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      setCardCollapsed(storageKey, next);
      return next;
    });
  }, [storageKey]);
  return { collapsed, toggle };
}
