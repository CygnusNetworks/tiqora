import { afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { DashboardConflictError, dashboardKeys, dashboardsApi, type Dashboard } from "@/lib/dashboardsApi";
import { demoDashboard, demoDefaultWidgets } from "@/demo/dashboardsMock";
import { toEditWidget } from "../model";
import { registerCoreWidgets } from "../widgets";
import { draftKey, writeDraft } from "./draft";
import { useEditSession } from "./useEditSession";

const DASH: Dashboard = demoDashboard({ id: 1, name: "Mein Start", version: 3, widgets: demoDefaultWidgets() });
const KEY = draftKey(5, 1);
let client: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
    </QueryClientProvider>
  );
}
const setup = (d: Dashboard = DASH) => renderHook(() => useEditSession(d, 5), { wrapper });
const moveFirst = { type: "move", id: "demo-w-open", to: { x: 1, y: 0 }, bp: "lg" } as const;

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  localStorage.clear();
  registerCoreWidgets();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("starts clean from the dashboard: name, widgets in array order, base version", () => {
  const { result } = setup();
  expect(result.current.state.name).toBe("Mein Start");
  expect(result.current.state.baseVersion).toBe(3);
  expect(result.current.state.widgets.map((w) => w.id)).toEqual(DASH.widgets.map((w) => w.id));
  expect(result.current.state.dirty).toBe(false);
  expect(result.current.draftSavedAt).toBeNull();
  expect(result.current.restoreAvailable).toBeNull();
});

test("the reducer knows the registered sizes: a counter resize is clamped to its max [4,3]", () => {
  const { result } = setup();
  act(() => result.current.dispatch({ type: "resize", id: "demo-w-open", size: { w: 9, h: 7 }, bp: "lg" }));
  expect(result.current.state.widgets[0].layout.lg).toMatchObject({ w: 4, h: 3 });
  act(() => result.current.dispatch({ type: "resize", id: "demo-w-open", size: { w: 1, h: 1 }, bp: "lg" }));
  expect(result.current.state.widgets[0].layout.lg).toMatchObject({ w: 2, h: 2 });
});

test("the draft is written 500 ms after the last change", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-10T08:42:00Z"));
  const { result } = setup();
  act(() => result.current.dispatch(moveFirst));
  await act(() => vi.advanceTimersByTimeAsync(499));
  expect(localStorage.getItem(KEY)).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(2));
  const stored = JSON.parse(localStorage.getItem(KEY)!);
  expect(stored).toMatchObject({ baseVersion: 3, name: "Mein Start", savedAt: Date.parse("2026-10-10T08:42:00.000Z") /* the time of the change */ });
  expect(stored.widgets[0].layout.lg.x).toBe(1);
  expect(result.current.draftSavedAt).toBe(stored.savedAt);
});

test("blocked storage: no draft time, the edit itself works", async () => {
  vi.useFakeTimers();
  vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  const { result } = setup();
  act(() => result.current.dispatch(moveFirst));
  await act(() => vi.advanceTimersByTimeAsync(600));
  expect(result.current.state.widgets[0].layout.lg.x).toBe(1);
  expect(result.current.draftSavedAt).toBeNull();
});

test("undo back to the saved state removes this session's draft", async () => {
  vi.useFakeTimers();
  const { result } = setup();
  act(() => result.current.dispatch(moveFirst));
  await act(() => vi.advanceTimersByTimeAsync(600));
  expect(localStorage.getItem(KEY)).not.toBeNull();
  act(() => result.current.dispatch({ type: "undo" }));
  await act(() => vi.advanceTimersByTimeAsync(600));
  expect(localStorage.getItem(KEY)).toBeNull();
  expect(result.current.draftSavedAt).toBeNull();
});

test("a draft of the same base version is offered; restore applies it as one undoable step", () => {
  const widgets = DASH.widgets.slice(0, 2).map(toEditWidget);
  writeDraft(5, 1, { baseVersion: 3, savedAt: 1, name: "Entwurf", widgets });
  const { result } = setup();
  expect(result.current.restoreAvailable?.name).toBe("Entwurf");
  act(() => result.current.restore());
  expect(result.current.state.widgets).toEqual(widgets);
  expect(result.current.state.name).toBe("Entwurf");
  expect(result.current.state.dirty).toBe(true);
  expect(result.current.state.past).toHaveLength(1);
  expect(result.current.restoreAvailable).toBeNull();
});

test("a draft of an older base version is dropped silently", () => {
  writeDraft(5, 1, { baseVersion: 2, savedAt: 1, name: "Alt", widgets: [] });
  const { result } = setup();
  expect(result.current.restoreAvailable).toBeNull();
  expect(localStorage.getItem(KEY)).toBeNull();
});

test("dropDraft forgets the offered draft", () => {
  writeDraft(5, 1, { baseVersion: 3, savedAt: 1, name: "Entwurf", widgets: [] });
  const { result } = setup();
  act(() => result.current.dropDraft());
  expect(result.current.restoreAvailable).toBeNull();
  expect(localStorage.getItem(KEY)).toBeNull();
});

test("save sends the session in array order with the base version, clears the draft and updates the cache", async () => {
  const saved = demoDashboard({ ...DASH, version: 4 });
  const save = vi.spyOn(dashboardsApi, "save").mockResolvedValue(saved);
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const { result } = setup();
  act(() => result.current.dispatch(moveFirst));
  writeDraft(5, 1, { baseVersion: 3, savedAt: 1, name: "x", widgets: [] });
  let outcome = "";
  await act(async () => {
    outcome = await result.current.save();
  });
  expect(outcome).toBe("saved");
  const body = save.mock.calls[0][1];
  expect(save.mock.calls[0][0]).toBe(1);
  expect(body).toMatchObject({ name: "Mein Start", icon: null, version: 3 });
  expect(body.widgets.map((w) => w.id)).toEqual(result.current.state.widgets.map((w) => w.id));
  expect(body.widgets[0]).toEqual({ ...result.current.state.widgets[0] });
  expect(localStorage.getItem(KEY)).toBeNull();
  expect(client.getQueryData(dashboardKeys.one(1))).toEqual(saved);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: dashboardKeys.all });
});

test("a layout the server would call too tall is not sent", async () => {
  const save = vi.spyOn(dashboardsApi, "save");
  const tall = demoDashboard({
    ...DASH,
    widgets: [{ ...DASH.widgets[0], layout: { lg: { x: 0, y: 999, w: 3, h: 2 } } }],
  });
  const { result } = setup(tall);
  let outcome = "";
  await act(async () => {
    outcome = await result.current.save();
  });
  expect(outcome).toBe("invalid");
  expect(result.current.saveError).toBe("Das Layout ist zu hoch.");
  expect(save).not.toHaveBeenCalled();
});

test("a conflict keeps the session and the draft and reports the current version", async () => {
  vi.spyOn(dashboardsApi, "save").mockRejectedValue(new DashboardConflictError("Dieses Dashboard wurde inzwischen geändert.", 4));
  writeDraft(5, 1, { baseVersion: 3, savedAt: 1, name: "x", widgets: [] });
  const { result } = setup();
  act(() => result.current.dispatch(moveFirst));
  let outcome = "";
  await act(async () => {
    outcome = await result.current.save();
  });
  expect(outcome).toBe("conflict");
  expect(result.current.conflictVersion).toBe(4);
  expect(result.current.state.widgets[0].layout.lg.x).toBe(1);
  expect(localStorage.getItem(KEY)).not.toBeNull();
});

test("rebaseDraft keeps the session as a draft of the newer version, marked as replacing it, dated by the change", () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-10T08:42:00Z"));
  const { result } = setup();
  act(() => result.current.dispatch(moveFirst));
  vi.setSystemTime(new Date("2026-10-10T08:50:00Z"));
  act(() => result.current.rebaseDraft(4));
  const stored = JSON.parse(localStorage.getItem(KEY)!);
  expect(stored.baseVersion).toBe(4);
  expect(stored.savedAt).toBe(Date.parse("2026-10-10T08:42:00Z"));
  expect(stored.overwrites).toEqual({ version: 4, at: Date.parse("2026-10-10T08:50:00Z") });
  expect(stored.widgets[0].layout.lg.x).toBe(1);
});

test("restoring a conflict draft marks the session as replacing the newer version until it is clean again", () => {
  const widgets = DASH.widgets.map(toEditWidget);
  widgets[0] = { ...widgets[0], layout: { lg: { x: 1, y: 0, w: 3, h: 2 } } };
  writeDraft(5, 1, { baseVersion: 3, savedAt: 1, name: "Mein Start", widgets, overwrites: { version: 3, at: 2 } });
  const { result } = setup();
  expect(result.current.restoreAvailable?.overwrites).toEqual({ version: 3, at: 2 });
  expect(result.current.pendingOverwrite).toBeNull();
  act(() => result.current.restore());
  expect(result.current.pendingOverwrite).toEqual({ version: 3, at: 2 });
  act(() => result.current.dispatch({ type: "undo" }));
  expect(result.current.pendingOverwrite).toBeNull();
});

test("an offered draft stays offered and stored while the user edits without deciding", async () => {
  vi.useFakeTimers();
  writeDraft(5, 1, { baseVersion: 3, savedAt: 1, name: "Entwurf", widgets: [] });
  const before = localStorage.getItem(KEY);
  const { result } = setup();
  act(() => result.current.dispatch(moveFirst));
  await act(() => vi.advanceTimersByTimeAsync(600));
  expect(result.current.restoreAvailable?.name).toBe("Entwurf");
  expect(localStorage.getItem(KEY)).toBe(before);
  expect(result.current.draftSavedAt).toBeNull();
});

test("leaving within 500 ms of a change still keeps it as a draft; after discard nothing is kept", () => {
  const first = setup();
  act(() => first.result.current.dispatch(moveFirst));
  first.unmount();
  expect(JSON.parse(localStorage.getItem(KEY)!).widgets[0].layout.lg.x).toBe(1);
  localStorage.clear();
  const second = setup();
  act(() => second.result.current.dispatch(moveFirst));
  act(() => second.result.current.discard());
  second.unmount();
  expect(localStorage.getItem(KEY)).toBeNull();
});

test("a 422 with a widget path marks that widget and keeps the message", async () => {
  vi.spyOn(dashboardsApi, "save").mockRejectedValue(
    new ApiError(422, { detail: { message: "Der Wert muss zwischen 1 und 50 liegen.", path: "widgets[6].settings.limit" } }, "/api/v1/dashboards/1"),
  );
  const { result } = setup();
  let outcome = "";
  await act(async () => {
    outcome = await result.current.save();
  });
  expect(outcome).toBe("invalid");
  expect(result.current.invalidWidgetId).toBe("demo-w-list-open");
  expect(result.current.invalidMessage).toBe("Der Wert muss zwischen 1 und 50 liegen.");
  expect(result.current.invalidPath).toBe("settings.limit");
  expect(result.current.saveError).toBeNull();
});

test("a 422 without a widget path and other failures show a message in the toolbar", async () => {
  const save = vi
    .spyOn(dashboardsApi, "save")
    .mockRejectedValueOnce(new ApiError(422, { detail: { message: "Höchstens 40 Widgets.", path: "widgets" } }, "/x"))
    .mockRejectedValueOnce(new Error("network"));
  const { result } = setup();
  let outcome = "";
  await act(async () => {
    outcome = await result.current.save();
  });
  expect(outcome).toBe("invalid");
  expect(result.current.invalidWidgetId).toBeNull();
  expect(result.current.saveError).toBe("Höchstens 40 Widgets.");
  await act(async () => {
    outcome = await result.current.save();
  });
  expect(outcome).toBe("error");
  expect(result.current.saveError).toBe("Das Dashboard konnte nicht gespeichert werden.");
  expect(save).toHaveBeenCalledTimes(2);
});
