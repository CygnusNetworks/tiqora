import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { demoTab } from "@/demo/dashboardsMock";
import type { DashboardTab } from "@/lib/dashboardsApi";
import { LAST_DASHBOARD_KEY, useSelectedDashboard } from "./useSelectedDashboard";

const auth = vi.hoisted(() => ({ user: { is_admin: false } }));
vi.mock("@/auth/AuthContext", () => ({ useOptionalAuth: () => auth }));

const TABS = [demoTab({ id: 1 }), demoTab({ id: 2, name: "Zwei" })];

function hook(tabs: DashboardTab[] | undefined, search: { d?: number }) {
  return renderHook(({ t, s }) => useSelectedDashboard(t, s), { initialProps: { t: tabs, s: search } });
}

beforeEach(() => {
  localStorage.clear();
  auth.user = { is_admin: false };
});
afterEach(() => vi.restoreAllMocks());

describe("useSelectedDashboard", () => {
  it("null until the tabs are known, then the first tab", () => {
    const { result, rerender } = hook(undefined, {});
    expect(result.current.selectedId).toBeNull();
    rerender({ t: TABS, s: {} });
    expect(result.current.selectedId).toBe(1);
  });

  it("prefers ?d= (visible) over the remembered one, and the remembered one over the first tab", () => {
    localStorage.setItem(LAST_DASHBOARD_KEY, "2");
    expect(hook(TABS, {}).result.current.selectedId).toBe(2);
    localStorage.setItem(LAST_DASHBOARD_KEY, "1");
    expect(hook(TABS, { d: 2 }).result.current.selectedId).toBe(2);
    localStorage.setItem(LAST_DASHBOARD_KEY, "77"); // gone since
    expect(hook(TABS, {}).result.current.selectedId).toBe(1);
  });

  it("an invisible ?d= is ignored for agents; admins try it until it is rejected", () => {
    expect(hook(TABS, { d: 9 }).result.current.selectedId).toBe(1);
    auth.user = { is_admin: true };
    const { result } = hook(TABS, { d: 9 });
    expect(result.current.selectedId).toBe(9);
    act(() => result.current.reject(9));
    expect(result.current.selectedId).toBe(1);
  });

  it("select wins over ?d=, is remembered, and a new ?d= wins again", () => {
    const { result, rerender } = hook(TABS, { d: 1 });
    act(() => result.current.select(2));
    expect(result.current.selectedId).toBe(2);
    expect(localStorage.getItem(LAST_DASHBOARD_KEY)).toBe("2");
    rerender({ t: TABS, s: { d: 1 } });
    expect(result.current.selectedId).toBe(2);
    rerender({ t: TABS, s: {} });
    rerender({ t: TABS, s: { d: 1 } });
    expect(result.current.selectedId).toBe(1);
  });

  it("storage errors are swallowed", () => {
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { result } = hook(TABS, {});
    expect(result.current.selectedId).toBe(1);
    act(() => result.current.select(2));
    expect(result.current.selectedId).toBe(2);
  });
});
