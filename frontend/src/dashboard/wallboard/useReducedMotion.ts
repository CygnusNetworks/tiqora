import { useEffect, useState } from "react";

/** Follows `prefers-reduced-motion: reduce` (false where `matchMedia` is missing). */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    try {
      return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    let query: MediaQueryList | undefined;
    try {
      query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    } catch {
      query = undefined;
    }
    if (!query?.addEventListener) return;
    const onChange = () => setReduced(query!.matches);
    query.addEventListener("change", onChange);
    return () => query!.removeEventListener("change", onChange);
  }, []);
  return reduced;
}
