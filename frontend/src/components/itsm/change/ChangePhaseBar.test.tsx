import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { PHASE_PATH, phaseSteps } from "@/lib/changePhases";
import type { ChangePhase, ChangeType } from "@/lib/changeApi";
import { ChangePhaseBar } from "./ChangePhaseBar";

beforeEach(async () => {
  await i18n.changeLanguage("de");
});
afterEach(cleanup);

function renderBar(changeType: ChangeType, phase: ChangePhase) {
  render(
    <I18nextProvider i18n={i18n}>
      <ChangePhaseBar changeType={changeType} phase={phase} />
    </I18nextProvider>,
  );
  return screen.getByTestId("change-phase-bar");
}

const labels = (bar: HTMLElement) => within(bar).getAllByRole("listitem").map((li) => li.textContent);
const states = (bar: HTMLElement) => within(bar).getAllByRole("listitem").map((li) => li.getAttribute("data-state"));

describe("ChangePhaseBar", () => {
  it("draws the whole path of a normal change", () => {
    const bar = renderBar("normal", "assessment");
    expect(labels(bar)).toEqual(["Entwurf", "Eingereicht", "Bewertung", "Freigabe", "Geplant", "Umsetzung", "Nachbetrachtung", "Abgeschlossen"]);
    expect(states(bar)).toEqual(["done", "done", "current", "upcoming", "upcoming", "upcoming", "upcoming", "upcoming"]);
    expect(within(bar).getByText("Bewertung")).toHaveAttribute("aria-current", "step");
  });

  it("does not draw the skipped approval steps as done for an emergency post-approval", () => {
    expect(phaseSteps("emergency", "implementing", true).map((s) => s.state)).toEqual(["done", "upcoming", "upcoming", "current", "upcoming", "upcoming"]);
    expect(phaseSteps("emergency", "implementing").map((s) => s.state)).toEqual(["done", "done", "done", "current", "upcoming", "upcoming"]);
  });

  it("skips submission and assessment for a standard change", () => {
    const bar = renderBar("standard", "draft");
    expect(labels(bar)).toEqual(["Entwurf", "Geplant", "Umsetzung", "Abgeschlossen"]);
    expect(states(bar)).toEqual(["current", "upcoming", "upcoming", "upcoming"]);
  });

  it("goes from the draft straight to the approval for an emergency change", () => {
    const bar = renderBar("emergency", "implementing");
    expect(labels(bar)).toEqual(["Entwurf", "Freigabe", "Geplant", "Umsetzung", "Nachbetrachtung", "Abgeschlossen"]);
    expect(states(bar)).toEqual(["done", "done", "done", "current", "upcoming", "upcoming"]);
  });

  it("marks every step done once the change is closed", () => {
    expect(states(renderBar("normal", "closed")).filter((s) => s !== "done")).toEqual(["current"]);
  });

  it("adds the review step to a standard change that is in review", () => {
    const bar = renderBar("standard", "review");
    expect(labels(bar)).toEqual(["Entwurf", "Geplant", "Umsetzung", "Nachbetrachtung", "Abgeschlossen"]);
    expect(states(bar)).toEqual(["done", "done", "done", "current", "upcoming"]);
  });

  it("shows a rejection after the approval it came from", () => {
    const bar = renderBar("normal", "rejected");
    expect(labels(bar).at(-1)).toBe("Abgelehnt");
    expect(states(bar)).toEqual(["done", "done", "done", "done", "upcoming", "upcoming", "upcoming", "upcoming", "current"]);
    expect(within(bar).getByText("Abgelehnt").className).toContain("text-danger");
  });

  it("shows a cancellation as the last step without finished steps", () => {
    const bar = renderBar("normal", "cancelled");
    expect(labels(bar).at(-1)).toBe("Abgebrochen");
    expect(states(bar).slice(0, -1).every((s) => s === "upcoming")).toBe(true);
  });

  it("labels the list for assistive technology", () => {
    expect(renderBar("normal", "draft")).toHaveAttribute("aria-label", "Phasen dieses Changes");
  });
});

describe("phaseSteps", () => {
  it("only uses phases that exist and always ends a path in closed", () => {
    for (const type of ["standard", "normal", "emergency"] as const) {
      expect(PHASE_PATH[type][0]).toBe("draft");
      expect(PHASE_PATH[type].at(-1)).toBe("closed");
      expect(phaseSteps(type, "draft")[0].state).toBe("current");
    }
  });
});
