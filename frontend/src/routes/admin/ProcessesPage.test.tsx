import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ProcessesPage } from "./ProcessesPage";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, params, children, ...rest }: { to: string; params?: Record<string, string>; children: ReactNode; [k: string]: unknown }) => (
    <a href={Object.entries(params ?? {}).reduce((acc, [k, v]) => acc.replace(`$${k}`, v), to)} {...rest}>
      {children}
    </a>
  ),
}));

const { processes, status } = vi.hoisted(() => ({ processes: vi.fn(), status: vi.fn() }));
vi.mock("@/lib/deployStatusApi", () => ({
  deployStatusApi: { processes, status, deploy: vi.fn() },
}));

const rows = [
  { entity_id: "Process-a", name: "Bestellung", state_entity_id: "S1", state_name: "Active", activity_count: 4, start_interfaces: ["AgentInterface"] },
  { entity_id: "Process-b", name: "Altprozess", state_entity_id: "S2", state_name: "Inactive", activity_count: 2, start_interfaces: [] },
  { entity_id: "Process-c", name: "Auslauf", state_entity_id: "S3", state_name: "FadeAway", activity_count: 1, start_interfaces: [] },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ProcessesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("ProcessesPage", () => {
  beforeEach(() => {
    processes.mockReset().mockResolvedValue(rows);
    status.mockReset().mockResolvedValue({
      process: {
        pending_changes: 3,
        running_live: false,
        revision: { id: 1, source: "initial", entity_count: 3, deployed_at: "2026-10-08T10:00:00Z", deployed_by: null },
      },
      acl: { pending_changes: 0, running_live: false, revision: null },
      ownership_active: false,
      can_deploy_from_tiqora: false,
    });
  });

  it("lists every process with its state, including inactive and fading ones, and the activity count", async () => {
    renderPage();
    expect(await screen.findByTestId("process-link-Process-a")).toHaveAttribute("href", "/admin/processes/Process-a");
    expect(screen.getByTestId("process-state-Process-a")).toHaveTextContent(i18n.t("admin.processes.states.Active"));
    expect(screen.getByTestId("process-state-Process-b")).toHaveTextContent(i18n.t("admin.processes.states.Inactive"));
    expect(screen.getByTestId("process-state-Process-c")).toHaveTextContent(i18n.t("admin.processes.states.FadeAway"));
    const rowA = screen.getByTestId("process-link-Process-a").closest("tr") as HTMLElement;
    expect(within(rowA).getByText("4")).toBeInTheDocument();
  });

  it("shows who can start each process", async () => {
    renderPage();
    await screen.findByTestId("process-link-Process-a");
    expect(screen.getByTestId("process-interfaces-Process-a")).toHaveTextContent(i18n.t("admin.processes.interfaceAgent"));
    expect(screen.getByTestId("process-interfaces-Process-b")).toHaveTextContent("—");
  });

  it("shows the process deploy status card", async () => {
    renderPage();
    await screen.findByTestId("process-link-Process-a");
    expect(await screen.findByTestId("deploy-status-process")).toBeInTheDocument();
    expect(screen.getByTestId("deploy-status-pending")).toHaveTextContent("3");
  });
});
