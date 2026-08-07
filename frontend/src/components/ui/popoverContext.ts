import { createContext, useContext } from "react";

type PopoverContextValue = { close: () => void };

export const PopoverContext = createContext<PopoverContextValue | null>(null);

/** Lets `Popover` panel content dismiss itself, e.g. after a successful write. */
export function usePopoverClose(): () => void {
  const ctx = useContext(PopoverContext);
  return ctx?.close ?? (() => {});
}
