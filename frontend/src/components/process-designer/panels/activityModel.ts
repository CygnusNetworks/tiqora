// Pure helpers for the step panel: Znuny stores an activity's forms as {"1": id, "2": id, ...}.
export function dialogIdsOf(config: Record<string, unknown>): string[] {
  const map = (config.ActivityDialog ?? {}) as Record<string, unknown>;
  return Object.keys(map)
    .sort((a, b) => Number(a) - Number(b))
    .map((k) => map[k])
    .filter((v): v is string => typeof v === "string");
}

/** Renumbers to the consecutive string keys "1".."n" Znuny expects. */
export function renumberDialogs(ids: string[]): Record<string, string> {
  return Object.fromEntries(ids.map((id, i) => [String(i + 1), id]));
}

export function moveItem<T>(list: T[], index: number, delta: -1 | 1): T[] {
  const to = index + delta;
  if (to < 0 || to >= list.length) return list;
  const next = [...list];
  [next[index], next[to]] = [next[to], next[index]];
  return next;
}

/** Empty form as Znuny's "new dialog" screen creates it. */
export const NEW_DIALOG_CONFIG = {
  DescriptionShort: "",
  DescriptionLong: "",
  Fields: {},
  FieldOrder: [],
  Interface: ["AgentInterface"],
  Permission: "",
  RequiredLock: "0",
  SubmitAdviceText: "",
  SubmitButtonText: "",
} as const;
