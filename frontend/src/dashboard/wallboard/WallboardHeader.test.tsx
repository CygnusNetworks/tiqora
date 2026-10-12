import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { WallboardHeader } from "./WallboardHeader";

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
  vi.setSystemTime(new Date("2026-10-10T10:42:30"));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const mount = (props: Partial<Parameters<typeof WallboardHeader>[0]> = {}) =>
  render(
    <I18nextProvider i18n={i18n}>
      <WallboardHeader name="Leitstand" generatedAt="2026-10-10T10:42:18" stale={false} heightPx={96} {...props} />
    </I18nextProvider>,
  );

describe("WallboardHeader", () => {
  it("shows the name, 'aktualisiert vor 12 s', the position and a clock", () => {
    mount({ position: { index: 1, count: 3 } });
    expect(screen.getByRole("heading", { level: 1, name: "Leitstand" })).toBeVisible();
    expect(screen.getByTestId("wallboard-status")).toHaveTextContent("Wallboard · aktualisiert vor 12 s · 2 / 3");
    expect(screen.getByTestId("wallboard-clock")).toHaveTextContent("10:42");
  });

  it("omits the position for a single dashboard and counts minutes after a minute", () => {
    mount({ generatedAt: "2026-10-10T10:39:30" });
    expect(screen.getByTestId("wallboard-status")).toHaveTextContent("Wallboard · aktualisiert vor 3 min");
    expect(screen.getByTestId("wallboard-status")).not.toHaveTextContent("/");
  });

  it("the age and the clock tick", () => {
    mount();
    act(() => void vi.advanceTimersByTime(40_000));
    expect(screen.getByTestId("wallboard-status")).toHaveTextContent("aktualisiert vor 52 s");
    expect(screen.getByTestId("wallboard-clock")).toHaveTextContent("10:43");
  });

  it("stale data shows 'Verbindung unterbrochen – Stand 10:41' in danger, without the position", () => {
    mount({ stale: true, generatedAt: "2026-10-10T10:41:05", position: { index: 0, count: 2 } });
    const status = screen.getByTestId("wallboard-status");
    expect(status).toHaveTextContent("Verbindung unterbrochen – Stand 10:41");
    expect(status).toHaveClass("text-danger");
    expect(status).not.toHaveTextContent("1 / 2");
  });

  it("before the first data it says so instead of a bogus age", () => {
    mount({ generatedAt: null });
    expect(screen.getByTestId("wallboard-status")).toHaveTextContent("Wallboard · Wird geladen …");
  });
});
