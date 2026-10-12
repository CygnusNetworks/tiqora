import { useEffect, useState } from "react";

/** Below this width the designer is a read-only list (spec: no canvas editing on phones). */
export const PHONE_MAX_WIDTH = 767;
const QUERY = `(max-width: ${PHONE_MAX_WIDTH}px)`;

function read(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia(QUERY).matches;
}

/** True on phone widths (< 768 px). Without `matchMedia` (tests, SSR) it is false. */
export function usePhoneWidth(): boolean {
  const [phone, setPhone] = useState(read);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(QUERY);
    const onChange = () => setPhone(mq.matches);
    onChange();
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return phone;
}
