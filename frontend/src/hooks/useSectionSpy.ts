import { useEffect, useState } from "react";

/** Trigger line below the scroll container's top: a section is "active" once
 *  its top edge scrolls above this offset. Larger when the page reserves a
 *  sticky header. */
const ACTIVE_OFFSET = 80;

/**
 * Tracks which vertical section of a scrolling region is currently in view.
 *
 * The app scrolls inside `<main id="main">` (see components/layout/Layout.tsx),
 * not the window, so callers pass the ids of their page's region anchors and
 * this hook reads that element's scroll position.
 *
 * Returns the id of the last section whose top edge has passed the trigger
 * line (falling back to the first id at the very top), or `null` when no
 * scroll container / sections exist (e.g. under jsdom).
 *
 * `sectionIds` must be ordered top-to-bottom to match DOM order.
 */
export function useSectionSpy(sectionIds: string[], rootId = "main"): string | null {
  // Depend on a joined key instead of the array reference so an inline array
  // literal in the caller (a fresh value every render) does not re-subscribe.
  const sectionKey = sectionIds.join("|");
  const [activeId, setActiveId] = useState<string | null>(null);

  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    if (!sectionKey) return undefined;
    const root = document.getElementById(rootId);
    if (!root) return undefined;

    const ids = sectionKey.split("|");
    let raf = 0;

    const update = () => {
      const rootTop = root.getBoundingClientRect().top;
      let active: string | null = null;
      for (const id of ids) {
        const el = document.getElementById(id);
        if (!el) continue;
        if (el.getBoundingClientRect().top - rootTop <= ACTIVE_OFFSET) {
          active = id;
        } else {
          break; // ids are ordered top-to-bottom in the DOM.
        }
      }
      setActiveId(active ?? ids[0] ?? null);
    };

    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };

    update();
    root.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      cancelAnimationFrame(raf);
      root.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, [sectionKey, rootId]);

  return activeId;
}
