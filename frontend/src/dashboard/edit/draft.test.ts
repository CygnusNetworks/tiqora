import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { EditWidget } from "../model";
import { clearDraft, draftKey, readDraft, writeDraft, type Draft } from "./draft";

const widget: EditWidget = {
  id: "w-counter-1",
  type_key: "core.counter",
  type_version: 1,
  title_override: null,
  settings: { tone: "danger" },
  pinned: false,
  layout: { lg: { x: 0, y: 0, w: 3, h: 2 } },
};
const draft: Draft = { baseVersion: 3, savedAt: Date.UTC(2026, 9, 10, 8, 42), name: "Mein Start", widgets: [widget] };
const KEY = "tiqora.dashboard.draft.5.1";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

test("the key names user and dashboard", () => {
  expect(draftKey(5, 1)).toBe(KEY);
});

test("write then read the same base version round-trips", () => {
  expect(writeDraft(5, 1, draft)).toBe(true);
  expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual(draft);
  expect(readDraft(5, 1, 3)).toEqual(draft);
});

test("a draft of an older base version is removed and not offered", () => {
  writeDraft(5, 1, draft);
  expect(readDraft(5, 1, 4)).toBeNull();
  expect(localStorage.getItem(KEY)).toBeNull();
});

test("a draft of a newer base version (reload still running) is kept but not offered", () => {
  writeDraft(5, 1, { ...draft, baseVersion: 4, overwrites: { version: 4, at: 1 } });
  expect(readDraft(5, 1, 3)).toBeNull();
  expect(localStorage.getItem(KEY)).not.toBeNull();
  expect(readDraft(5, 1, 4)?.overwrites).toEqual({ version: 4, at: 1 });
});

test.each([
  ["not JSON", "{oops"],
  ["no widgets", JSON.stringify({ baseVersion: 3, savedAt: 1, name: "x" })],
  ["widget without layout", JSON.stringify({ baseVersion: 3, savedAt: 1, name: "x", widgets: [{ id: "a", type_key: "core.notes" }] })],
  ["savedAt missing", JSON.stringify({ baseVersion: 3, name: "x", widgets: [] })],
  ["broken overwrite marker", JSON.stringify({ baseVersion: 3, savedAt: 1, name: "x", widgets: [], overwrites: { version: "4" } })],
])("a malformed entry (%s) is removed", (_label, raw) => {
  localStorage.setItem(KEY, raw);
  expect(readDraft(5, 1, 3)).toBeNull();
  expect(localStorage.getItem(KEY)).toBeNull();
});

test("blocked storage: write answers false, read answers null, clear does not throw", () => {
  vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(window.localStorage, "removeItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  expect(writeDraft(5, 1, draft)).toBe(false);
  expect(readDraft(5, 1, 3)).toBeNull();
  expect(() => clearDraft(5, 1)).not.toThrow();
});

test("clear removes only this dashboard's draft", () => {
  writeDraft(5, 1, draft);
  writeDraft(5, 2, draft);
  clearDraft(5, 1);
  expect(localStorage.getItem(KEY)).toBeNull();
  expect(localStorage.getItem(draftKey(5, 2))).not.toBeNull();
});
