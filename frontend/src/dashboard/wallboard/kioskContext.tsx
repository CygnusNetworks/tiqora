import { createContext, useContext, type ReactNode } from "react";

const KioskContext = createContext(false);

/** The public wallboard page wraps its tree in this: no links, no authenticated helpers. */
export function KioskProvider({ children }: { children: ReactNode }) {
  return <KioskContext.Provider value={true}>{children}</KioskContext.Provider>;
}

/** True on a wallboard opened with a link (no login). A logged-in TV mode is not a kiosk. */
// eslint-disable-next-line react-refresh/only-export-components
export function useIsKiosk(): boolean {
  return useContext(KioskContext);
}
