/** Recently viewed ticket ids (agent zoom), persisted in localStorage. */

const STORAGE_KEY = "tiqora.lastViews";
const MAX_ITEMS = 12;

export type LastViewEntry = {
  id: number;
  tn?: string;
  title?: string;
  at: number;
};

function readRaw(): LastViewEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (x): x is LastViewEntry =>
          !!x &&
          typeof x === "object" &&
          typeof (x as LastViewEntry).id === "number" &&
          Number.isFinite((x as LastViewEntry).id),
      )
      .slice(0, MAX_ITEMS);
  } catch {
    return [];
  }
}

function writeRaw(entries: LastViewEntry[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX_ITEMS)));
  } catch {
    // best-effort only
  }
}

/** Record that the agent opened a ticket zoom. Newest first. */
export function recordTicketView(entry: {
  id: number;
  tn?: string | null;
  title?: string | null;
}): void {
  if (!Number.isFinite(entry.id) || entry.id <= 0) return;
  const prev = readRaw().filter((e) => e.id !== entry.id);
  const next: LastViewEntry[] = [
    {
      id: entry.id,
      tn: entry.tn ?? undefined,
      title: entry.title ?? undefined,
      at: Date.now(),
    },
    ...prev,
  ].slice(0, MAX_ITEMS);
  writeRaw(next);
}

/** Most recently viewed tickets (newest first). */
export function getLastViews(): LastViewEntry[] {
  return readRaw();
}
