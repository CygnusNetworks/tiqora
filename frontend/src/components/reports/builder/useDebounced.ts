import { useEffect, useState } from "react";

/** Mutable only so tests can shorten the wait. */
export const previewDebounce = { ms: 700 };

/**
 * `value` after it stayed unchanged for `previewDebounce.ms`. Changes from or to `null`
 * (nothing to preview yet / the report became incomplete) apply at once: there is no
 * earlier picture to keep steady, and waiting would only delay the first preview or the hint.
 */
export function useDebouncedPreview<T>(value: T | null): T | null {
  const [shown, setShown] = useState<T | null>(value);
  const key = JSON.stringify(value);
  useEffect(() => {
    if (JSON.stringify(shown) === key) return;
    if (value === null || shown === null) {
      setShown(value);
      return;
    }
    const timer = setTimeout(() => setShown(value), previewDebounce.ms);
    return () => clearTimeout(timer);
    // `value` is represented by `key`; `shown` is read only to compare
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return shown;
}
