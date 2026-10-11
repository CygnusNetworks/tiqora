import type { BuilderState, DimensionSlot } from "./builderState";

/** All dimensions in definition order: the column sits at `columnAt`, else last (as `toDefinition` emits them). */
export function ordered(state: BuilderState): DimensionSlot[] {
  if (state.columns.length === 0) return [...state.rows];
  const at = Math.min(state.columnAt ?? state.rows.length, state.rows.length);
  return [...state.rows.slice(0, at), state.columns[0], ...state.rows.slice(at)];
}

/** Makes `key` the series dimension (or none) without changing the order of the dimensions. */
export function withSeries(state: BuilderState, key: string | null): BuilderState {
  const all = ordered(state);
  const at = key === null ? -1 : all.findIndex((d) => d.key === key);
  if (key !== null && at < 0) return state;
  return {
    ...state,
    rows: all.filter((_, i) => i !== at),
    columns: at < 0 ? [] : [all[at]],
    columnAt: at < 0 ? null : at,
  };
}
