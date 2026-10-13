import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { CiPicker } from "./CiPicker";
import { makeCi } from "./testSupport";

const { cis, catalog, linkTypes } = vi.hoisted(() => ({ cis: vi.fn(), catalog: vi.fn(), linkTypes: vi.fn() }));
vi.mock("@/lib/itsmApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/itsmApi")>("@/lib/itsmApi");
  return { ...actual, itsmApi: { ...actual.itsmApi, cis, catalog, linkTypes } };
});

function renderPicker(props: Partial<React.ComponentProps<typeof CiPicker>> = {}) {
  const onPick = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CiPicker onClose={() => {}} onPick={onPick} linked={new Set()} {...props} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { onPick };
}

beforeEach(() => {
  cis.mockReset().mockResolvedValue({ items: [makeCi(1), makeCi(2)], total: 2, truncated: false });
  catalog.mockReset().mockResolvedValue({
    classes: [{ key: "computer", name: "Computer", icon: null, can_read: true, can_write: true }],
    deployment_states: [],
    incident_states: [],
  });
  linkTypes.mockReset().mockResolvedValue([
    { name: "RelevantTo", origin: "znuny", pointed: false, propagation: "none", source_name: "Relevant to", target_name: "Relevant to", source_label: "Betrifft", target_label: "Betrifft" },
    { name: "DependsOn", origin: "znuny", pointed: true, propagation: "source", source_name: "Depends on", target_name: "Dependency of", source_label: "Hängt ab von", target_label: "Voraussetzung für" },
  ]);
});
afterEach(cleanup);

describe("CiPicker", () => {
  it("lists CIs, searches with the typed text and picks a hit with the default link type", async () => {
    const { onPick } = renderPicker();
    expect(await screen.findByTestId("ci-picker-hit-1")).toHaveTextContent("srv-1.example.org");
    fireEvent.change(screen.getByTestId("ci-picker-search"), { target: { value: "srv-2" } });
    await waitFor(() => expect(cis).toHaveBeenLastCalledWith(expect.objectContaining({ q: "srv-2", limit: 20 }), expect.anything()));
    fireEvent.click(screen.getByTestId("ci-picker-hit-2"));
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0][0].ref).toBe("znuny:2");
    expect(onPick.mock.calls[0][1]).toBeNull();
  });

  it("moves through the hits with the arrow keys and picks with Enter", async () => {
    const { onPick } = renderPicker();
    await screen.findByTestId("ci-picker-hit-1");
    const search = screen.getByTestId("ci-picker-search");
    expect(screen.getByTestId("ci-picker-hit-1")).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(search, { key: "ArrowDown" });
    expect(screen.getByTestId("ci-picker-hit-2")).toHaveAttribute("aria-selected", "true");
    expect(search).toHaveAttribute("aria-activedescendant", expect.stringMatching(/-2$/));
    fireEvent.keyDown(search, { key: "ArrowDown" });
    expect(screen.getByTestId("ci-picker-hit-2")).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(search, { key: "ArrowUp" });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "Enter" });
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0][0].id).toBe(2);
  });

  it("filters by class and hands over the chosen link type", async () => {
    const { onPick } = renderPicker();
    await screen.findByTestId("ci-picker-hit-1");
    fireEvent.click(screen.getByTestId("ci-picker-class"));
    fireEvent.click(await screen.findByTestId("ci-picker-class-menu-option-computer"));
    await waitFor(() => expect(cis).toHaveBeenLastCalledWith(expect.objectContaining({ class: ["computer"] }), expect.anything()));
    fireEvent.click(screen.getByTestId("ci-picker-link-type"));
    fireEvent.click(await screen.findByTestId("ci-picker-link-type-menu-option-DependsOn"));
    fireEvent.click(screen.getByTestId("ci-picker-hit-1"));
    expect(onPick.mock.calls[0][1]).toBe("DependsOn");
  });

  it("does not pick an already linked CI", async () => {
    const { onPick } = renderPicker({ linked: new Set(["znuny:1"]) });
    const hit = await screen.findByTestId("ci-picker-hit-1");
    expect(hit).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(hit);
    fireEvent.keyDown(screen.getByTestId("ci-picker-search"), { key: "Enter" });
    expect(onPick).not.toHaveBeenCalled();
  });

  it("explains an unavailable CMDB in plain words", async () => {
    cis.mockRejectedValue(new ApiError(409, { detail: { code: "cmdb_not_installed" } }, "/x"));
    renderPicker();
    expect(await screen.findByTestId("ci-picker-unavailable")).toHaveTextContent(/CMDB/);
  });

  it("shows a link error from the caller and an empty result", async () => {
    cis.mockResolvedValue({ items: [], total: 0, truncated: false });
    renderPicker({ error: "Das CI konnte nicht verknüpft werden." });
    expect(await screen.findByTestId("ci-picker-empty")).toBeTruthy();
    expect(screen.getByTestId("ci-picker-error")).toHaveTextContent("nicht verknüpft");
  });
});
