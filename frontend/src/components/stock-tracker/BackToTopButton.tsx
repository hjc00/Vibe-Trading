import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowUp } from "lucide-react";
import { cn } from "@/lib/utils";

/** Scroll distance (px) past which the floating button should appear. */
const SHOW_AFTER = 400;

/**
 * Floating "back to top" action pinned to the bottom-right of the viewport.
 * The dashboard scrolls inside `<main id="main">`, so the button reads that
 * element's scroll position and scrolls it back to the top on click. Always
 * available — unlike the `xl`-only SectionNav outline.
 */
export function BackToTopButton() {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (typeof document === "undefined") return undefined;
    const root = document.getElementById("main");
    if (!root) return undefined;

    let raf = 0;
    const update = () => {
      setVisible(root.scrollTop > SHOW_AFTER);
    };
    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };

    root.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    update();
    return () => {
      cancelAnimationFrame(raf);
      root.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, []);

  const handleClick = () => {
    if (typeof document === "undefined") return;
    document.getElementById("main")?.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-hidden={!visible}
      tabIndex={visible ? 0 : -1}
      aria-label={t("stockTracker.navBackToTop")}
      title={t("stockTracker.navBackToTop")}
      className={cn(
        "fixed bottom-6 right-6 z-20 inline-flex h-9 w-9 items-center justify-center rounded-full border border-border/60 bg-card text-muted-foreground shadow-lg transition-all duration-200 hover:text-primary",
        visible
          ? "translate-y-0 opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          : "pointer-events-none translate-y-2 opacity-0",
      )}
    >
      <ArrowUp className="h-4 w-4" />
    </button>
  );
}
