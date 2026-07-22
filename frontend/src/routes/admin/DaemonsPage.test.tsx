import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { DaemonsPage } from "./DaemonsPage";
import type { DaemonServiceOut } from "@/lib/api";

const getDaemons = vi.fn();
const putDaemon = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getDaemons: (...args: unknown[]) => getDaemons(...args),
    putDaemon: (...args: unknown[]) => putDaemon(...args),
  },
}));

function poller(overrides: Partial<DaemonServiceOut> = {}): DaemonServiceOut {
  return {
    slug: "poller",
    enabled: true,
    toggleable: false,
    schedule: "interval",
    interval_seconds: 15,
    interval_overridden: false,
    daily_at: null,
    last_run_at: "2026-07-19T10:00:00+00:00",
    last_ok_at: "2026-07-19T10:00:00+00:00",
    last_error: null,
    last_result: null,
    ...overrides,
  };
}

function postmaster(overrides: Partial<DaemonServiceOut> = {}): DaemonServiceOut {
  return {
    slug: "postmaster",
    enabled: false,
    toggleable: true,
    schedule: "interval",
    interval_seconds: 60,
    interval_overridden: false,
    daily_at: null,
    last_run_at: null,
    last_ok_at: null,
    last_error: null,
    last_result: null,
    ...overrides,
  };
}

function gdprRetention(overrides: Partial<DaemonServiceOut> = {}): DaemonServiceOut {
  return {
    slug: "gdpr_retention",
    enabled: false,
    toggleable: true,
    schedule: "daily",
    interval_seconds: null,
    interval_overridden: false,
    daily_at: "03:00",
    last_run_at: null,
    last_ok_at: null,
    last_error: null,
    last_result: null,
    ...overrides,
  };
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <DaemonsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("DaemonsPage", () => {
  beforeEach(() => {
    getDaemons.mockReset();
    putDaemon.mockReset();
  });

  it("renders one row per service, poller not toggleable, daily services show their UTC time", async () => {
    getDaemons.mockResolvedValue({
      services: [poller(), postmaster(), gdprRetention()],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("daemon-row-poller")).toBeInTheDocument();
    });

    expect(screen.getByTestId("daemon-toggle-poller")).toBeChecked();
    expect(screen.getByTestId("daemon-toggle-poller")).toBeDisabled();
    expect(screen.getByTestId("daemon-status-poller")).toBeInTheDocument();

    expect(screen.getByTestId("daemon-row-postmaster")).toBeInTheDocument();
    expect(screen.getByTestId("daemon-toggle-postmaster")).not.toBeChecked();
    expect(screen.getByTestId("daemon-toggle-postmaster")).not.toBeDisabled();

    expect(screen.getByTestId("daemon-row-gdpr_retention")).toBeInTheDocument();
    expect(screen.getByText(/03:00/)).toBeInTheDocument();
  });

  it("toggling a service PUTs the flipped enabled value", async () => {
    getDaemons.mockResolvedValue({ services: [poller(), postmaster()] });
    putDaemon.mockResolvedValue(postmaster({ enabled: true }));

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("daemon-toggle-postmaster")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("daemon-toggle-postmaster"));

    await waitFor(() => expect(putDaemon).toHaveBeenCalledWith("postmaster", { enabled: true }));
  });

  it("editing the interval field and blurring PUTs the new interval_seconds", async () => {
    getDaemons.mockResolvedValue({ services: [poller(), postmaster()] });
    putDaemon.mockResolvedValue(postmaster({ interval_seconds: 120, interval_overridden: true }));

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("daemon-interval-postmaster")).toBeInTheDocument();
    });

    const input = screen.getByTestId("daemon-interval-postmaster");
    fireEvent.change(input, { target: { value: "120" } });
    fireEvent.blur(input);

    await waitFor(() =>
      expect(putDaemon).toHaveBeenCalledWith("postmaster", { interval_seconds: 120 }),
    );
  });

  it("shows a reset control when the interval is overridden", async () => {
    getDaemons.mockResolvedValue({
      services: [poller(), postmaster({ interval_seconds: 90, interval_overridden: true })],
    });

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("daemon-interval-reset-postmaster")).toBeInTheDocument();
    });
  });
});
