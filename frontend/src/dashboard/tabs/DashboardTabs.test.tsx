import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { demoTab } from "@/demo/dashboardsMock";
import { DashboardTabs } from "./DashboardTabs";

const TABS = [
  demoTab({ id: 1, name: "Mein Start" }),
  demoTab({ id: 2, name: "Team Support", kind: "shared", is_owner: false, owner_name: "Maya Rivera" }),
  demoTab({ id: 3, name: "Leitstand", kind: "template", locked: true, is_owner: false }),
];

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

describe("DashboardTabs", () => {
  it("selects on click and Enter-free activation, arrows only move the focus (wrapping, Home/End)", () => {
    const onSelect = vi.fn();
    render(<DashboardTabs tabs={TABS} selectedId={2} onSelect={onSelect} onCreate={() => {}} />);
    const team = screen.getByRole("tab", { name: "Team Support", selected: true });
    expect(team).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Mein Start" })).toHaveAttribute("tabindex", "-1");
    fireEvent.keyDown(team, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Mein Start" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Mein Start" }), { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Leitstand" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Leitstand" }), { key: "Home" });
    expect(screen.getByRole("tab", { name: "Mein Start" })).toHaveFocus();
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "Leitstand" }));
    expect(onSelect).toHaveBeenCalledWith(3);
  });

  it("with no tab selected (a template opened by link) the first tab is the tab stop", () => {
    render(<DashboardTabs tabs={TABS} selectedId={9} onSelect={() => {}} onCreate={() => {}} />);
    expect(screen.queryAllByRole("tab", { selected: true })).toHaveLength(0);
    expect(screen.getByRole("tab", { name: "Mein Start" })).toHaveAttribute("tabindex", "0");
  });

  it("the '+' button is outside the tab list and always active", () => {
    const onCreate = vi.fn();
    render(<DashboardTabs tabs={TABS} selectedId={1} onSelect={() => {}} onCreate={onCreate} />);
    const plus = screen.getByRole("button", { name: "Neues Dashboard" });
    expect(screen.getByRole("tablist", { name: "Dashboards" })).not.toContainElement(plus);
    expect(plus).toBeEnabled();
    fireEvent.click(plus);
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("describes locked templates and shared owners without changing the tab names", () => {
    render(<DashboardTabs tabs={TABS} selectedId={1} onSelect={() => {}} onCreate={() => {}} />);
    expect(screen.getByRole("tab", { name: "Leitstand" })).toHaveAccessibleDescription("Verbindlich");
    expect(screen.getByRole("tab", { name: "Team Support" })).toHaveAccessibleDescription("von Maya Rivera");
    expect(screen.getByRole("tab", { name: "Mein Start" })).not.toHaveAccessibleDescription();
  });
});
