import { useSyncExternalStore } from "react";

/**
 * "Show avatars" in ticket lists, the dashboard and the conversation —
 * remembered per browser like `./refineTone`, default on. A display
 * preference, so it never leaves the browser. Off means initials only (or no
 * avatar at all in lists) and no picture requests.
 *
 * A tiny module store so the account-menu toggle re-renders every list at
 * once (same pattern as `./callPopup`).
 */

const STORAGE_KEY = "tiqora-show-avatars";

function load(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "0";
  } catch {
    return true;
  }
}

let showAvatars = load();
const listeners = new Set<() => void>();

export function getShowAvatars(): boolean {
  return showAvatars;
}

export function setShowAvatars(on: boolean): void {
  showAvatars = on;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
    } catch {
      // private mode / SSR — ignore
    }
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useShowAvatars(): boolean {
  return useSyncExternalStore(subscribe, getShowAvatars, getShowAvatars);
}
