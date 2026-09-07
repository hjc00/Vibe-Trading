import { useCallback, useState } from "react";

const clampInt = (num: number, min: number, max?: number): number =>
  Math.max(min, max == null ? num : Math.min(max, num));

/**
 * Buffers the raw text of a numeric input so intermediate keystrokes that fall
 * outside [min, max] (e.g. "3" while typing "300" with min 5) don't snap the
 * field back to the bound mid-edit. The parent value is still updated (clamped)
 * on every change, so saving without blurring first persists the typed value;
 * the buffer clears on blur, where the display settles on the clamped value.
 */
export function useClampedIntInput(
  value: number,
  onCommit: (next: number) => void,
  min: number,
  max?: number,
): { value: string; onChange: (raw: string) => void; onBlur: () => void } {
  const [text, setText] = useState<string | null>(null);
  const onChange = useCallback(
    (raw: string) => {
      // Digits only (allow empty mid-edit); reject anything parse-hostile.
      if (!/^\d*$/.test(raw)) return;
      setText(raw);
      const num = parseInt(raw, 10);
      if (!Number.isNaN(num)) onCommit(clampInt(num, min, max));
    },
    [onCommit, min, max],
  );
  const onBlur = useCallback(() => setText(null), []);
  return { value: text ?? String(value), onChange, onBlur };
}
