import { useEffect, useState } from "react";

/** Mutable only so tests can shorten the wait before the editor asks for the count and the matches again. */
export const searchPreviewDebounce = { ms: 500 };

/** `value` after it stayed unchanged for `searchPreviewDebounce.ms`. */
export function useDebouncedValue<T>(value: T): T {
  const [shown, setShown] = useState(value);
  const key = JSON.stringify(value);
  useEffect(() => {
    if (JSON.stringify(shown) === key) return;
    const timer = setTimeout(() => setShown(value), searchPreviewDebounce.ms);
    return () => clearTimeout(timer);
    // `value` is represented by `key`; `shown` is read only to compare
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return shown;
}
