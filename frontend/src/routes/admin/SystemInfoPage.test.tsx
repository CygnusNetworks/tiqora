import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SystemInfoPage } from "./SystemInfoPage";
import type { SystemInfoOut } from "@/lib/api";

const getSystemInfo = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getSystemInfo: (...args: unknown[]) => getSystemInfo(...args),
  },
}));

function sysinfo(overrides: Partial<SystemInfoOut> = {}): SystemInfoOut {
  return {
    app: {
      name: "Tiqora",
      version: "0.1.0",
      git_sha: "abc1234",
      build_time: null,
      environment: "production",
      python_version: "3.12.7",
      hostname: "docker-virt6",
      server_time: "2026-07-27T12:00:00+00:00",
      started_at: "2026-07-25T12:00:00+00:00",
      uptime_seconds: 176460,
    },
    services: [
      {
        slug: "poller",
        enabled: true,
        toggleable: false,
        schedule: "interval",
        interval_seconds: 15,
        interval_overridden: false,
        daily_at: null,
        last_run_at: "2026-07-27T11:59:55+00:00",
        last_ok_at: "2026-07-27T11:59:55+00:00",
        last_error: null,
        last_result: null,
      },
    ],
    datastores: {
      database: {
        dialect: "postgresql",
        connected: true,
        version: "PostgreSQL 16.3",
        latency_ms: 0.4,
        size_bytes: 1_900_000_000,
      },
      redis: {
        connected: true,
        version: "7.2.5",
        used_memory_bytes: 34_000_000,
        clients: 8,
        latency_ms: 0.2,
      },
      search: {
        available: true,
        reason: null,
        version: "1.11.0",
        tickets_docs: 4217,
        kb_docs: 386,
        database_size_bytes: 92_000_000,
      },
    },
    containers: { available: false, reason: "Docker-Socket nicht gemountet", items: [] },
    host: { available: false, reason: "psutil nicht installiert" },
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
        <SystemInfoPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("SystemInfoPage", () => {
  beforeEach(() => {
    getSystemInfo.mockReset();
  });

  it("renders the overall banner, app info, a service row and datastore status", async () => {
    getSystemInfo.mockResolvedValue(sysinfo());

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("system-overall")).toBeInTheDocument();
    });

    // All green → overall status green.
    expect(screen.getByTestId("system-overall")).toHaveAttribute("data-status", "green");
    // Service row present with a status chip.
    expect(screen.getByTestId("system-service-poller")).toBeInTheDocument();
    // App build provenance surfaced.
    expect(screen.getByText("abc1234")).toBeInTheDocument();
    expect(screen.getByText("docker-virt6")).toBeInTheDocument();
    // Datastore index doc counts rendered (locale-agnostic thousands separator).
    expect(screen.getByText(/4[.,]217/)).toBeInTheDocument();
  });

  it("degrades gracefully when containers and host are unavailable", async () => {
    getSystemInfo.mockResolvedValue(sysinfo());

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("system-overall")).toBeInTheDocument();
    });

    expect(screen.getByText("Docker-Socket nicht gemountet")).toBeInTheDocument();
    expect(screen.getByText("psutil nicht installiert")).toBeInTheDocument();
  });

  it("marks overall status red when a datastore is down", async () => {
    const base = sysinfo();
    getSystemInfo.mockResolvedValue({
      ...base,
      datastores: {
        ...base.datastores,
        database: { ...base.datastores.database, connected: false },
      },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("system-overall")).toHaveAttribute("data-status", "red");
    });
  });

  it("shows an error state when the request fails", async () => {
    getSystemInfo.mockRejectedValue(new Error("boom"));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("admin-system-page")).toHaveTextContent(
        i18n.t("admin.systemInfo.loadError"),
      );
    });
  });
});
