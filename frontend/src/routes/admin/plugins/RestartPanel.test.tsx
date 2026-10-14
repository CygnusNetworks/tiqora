import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { restartStatus } from "@/test/pluginFixtures";
import { RestartPanel } from "./RestartPanel";

const restartStatusFn = vi.fn();
vi.mock("@/lib/pluginsApi", () => ({
  pluginsApi: { restartStatus: (...a: unknown[]) => restartStatusFn(...a) },
}));

function renderPanel(generation: number | null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RestartPanel generation={generation} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const running = (overrides = {}) =>
  restartStatus({
    finished_at: null,
    generation: 5,
    roles: [
      { role: "worker", old: 4, new: 5, state: "done" },
      { role: "api", old: 4, new: 4, state: "waiting" },
    ],
    ...overrides,
  });

async function tick(ms = 2100) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
  restartStatusFn.mockReset();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});
afterEach(() => vi.useRealTimers());

describe("RestartPanel", () => {
  it("polls until finished and offers a reload", async () => {
    restartStatusFn.mockResolvedValueOnce(running()).mockResolvedValue(restartStatus({ generation: 5 }));
    renderPanel(5);
    expect(await screen.findByTestId("plugin-restart-running")).toHaveTextContent("Tiqora wird neu gestartet");
    expect(screen.getByText("Hintergrunddienst")).toBeInTheDocument();
    expect(screen.getByText("Wartet")).toBeInTheDocument();
    // REL-RESTART-ALLJOBS: the panel says that running background tasks are waited for
    expect(screen.getByTestId("plugin-restart-waits-for-jobs")).toHaveTextContent(
      "wartet der Neustart dieses Dienstes, bis sie fertig ist",
    );
    await tick();
    expect(await screen.findByText("Tiqora wurde neu gestartet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Seite neu laden" })).toBeInTheDocument();
    const calls = restartStatusFn.mock.calls.length;
    await tick(6000);
    expect(restartStatusFn.mock.calls.length).toBe(calls); // polling stopped
  });

  it("keeps polling through network errors while the web interface restarts", async () => {
    restartStatusFn
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(restartStatus({ generation: 5 }));
    renderPanel(5);
    expect(await screen.findByText("Weboberfläche startet neu …")).toBeInTheDocument();
    await tick();
    expect(screen.getByText("Weboberfläche startet neu …")).toBeInTheDocument();
    await tick();
    expect(await screen.findByText("Tiqora wurde neu gestartet.")).toBeInTheDocument();
    expect(restartStatusFn).toHaveBeenCalledTimes(3);
  });

  it("ignores the previous restart until ours shows up", async () => {
    restartStatusFn.mockResolvedValueOnce(restartStatus({ generation: 4 })).mockResolvedValue(restartStatus({ generation: 5 }));
    renderPanel(5);
    expect(await screen.findByTestId("plugin-restart-running")).toBeInTheDocument();
    expect(screen.queryByText("Tiqora wurde neu gestartet.")).toBeNull();
    await tick();
    expect(await screen.findByText("Tiqora wurde neu gestartet.")).toBeInTheDocument();
  });

  it("shows the failure message", async () => {
    restartStatusFn.mockResolvedValue(
      running({ failed_message: "Der Dienst „api“ ist nicht zurückgekommen.", finished_at: "2026-10-12T08:05:00Z" }),
    );
    renderPanel(5);
    expect(await screen.findByRole("alert")).toHaveTextContent("Der Dienst „api“ ist nicht zurückgekommen.");
  });

  it("asks for a manual restart when no coordinated restart was recorded, without polling", async () => {
    // an old, finished coordinated restart must not read as "Tiqora wurde neu gestartet"
    restartStatusFn.mockResolvedValue(restartStatus({ generation: 9 }));
    renderPanel(null);
    expect(await screen.findByTestId("plugin-restart-manual")).toHaveTextContent(
      "Bitte starten Sie Tiqora neu, damit die Änderung wirksam wird.",
    );
    await tick(6000);
    expect(restartStatusFn).not.toHaveBeenCalled();
    expect(screen.queryByText("Tiqora wurde neu gestartet.")).toBeNull();
  });

  it("follows a newer restart that superseded ours", async () => {
    restartStatusFn.mockResolvedValue(restartStatus({ generation: 6 }));
    renderPanel(5);
    expect(await screen.findByText("Tiqora wurde neu gestartet.")).toBeInTheDocument();
  });

  it("gives up on network errors after five minutes", async () => {
    restartStatusFn.mockRejectedValue(new TypeError("Failed to fetch"));
    renderPanel(5);
    expect(await screen.findByText("Weboberfläche startet neu …")).toBeInTheDocument();
    await tick(5 * 60 * 1000 + 4000);
    expect(await screen.findByTestId("plugin-restart-error")).toHaveTextContent("nicht zurückgekommen");
  });
});
