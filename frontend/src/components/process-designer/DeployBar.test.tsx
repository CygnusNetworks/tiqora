import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { DeployStatus } from "@/lib/deployStatusApi";
import { DeployBar } from "./DeployBar";

const { status, deploy, validation } = vi.hoisted(() => ({ status: vi.fn(), deploy: vi.fn(), validation: vi.fn() }));
vi.mock("@/lib/deployStatusApi", () => ({ deployStatusApi: { status, deploy } }));
vi.mock("@/lib/processDesignerApi", () => ({ processDesignerApi: { validation } }));

const kind = (over: Partial<DeployStatus["process"]> = {}): DeployStatus["process"] => ({
  pending_changes: 1,
  running_live: false,
  revision: null,
  znuny_sync: { state: "not_needed", revision_id: null, message: "", since: null, overdue: false },
  deploy_mode: "local",
  ...over,
});
const body = (process = kind()): DeployStatus => ({
  process,
  acl: kind(),
  ownership_active: false,
  can_deploy_from_tiqora: true,
});
const issue = (severity: "error" | "warning") => ({
  code: "activity_dead_end",
  severity,
  entity_type: "Activity" as const,
  entity_id: "Activity-a",
  field: "",
  params: {},
});

function renderBar(props: Partial<React.ComponentProps<typeof DeployBar>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <DeployBar processEntityId="Process-1" {...props} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("DeployBar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    status.mockResolvedValue(body());
    validation.mockResolvedValue([]);
    deploy.mockResolvedValue({ action: "recorded", checksum: "x", kind: "process", mode: "local", revision_id: 9 });
  });

  it("has no deploy button where the server reports no deploy_mode", async () => {
    status.mockResolvedValue(body(kind({ deploy_mode: null })));
    renderBar();
    await screen.findByTestId("deploy-bar-status");
    expect(screen.queryByTestId("deploy-bar-deploy")).not.toBeInTheDocument();
  });

  it("checks first and deploys at once when there are only warnings", async () => {
    validation.mockResolvedValue([issue("warning")]);
    const onValidated = vi.fn();
    renderBar({ onValidated });
    fireEvent.click(await screen.findByTestId("deploy-bar-deploy"));
    await waitFor(() => expect(deploy).toHaveBeenCalledWith("process"));
    expect(validation.mock.invocationCallOrder[0]).toBeLessThan(deploy.mock.invocationCallOrder[0]);
    expect(onValidated).toHaveBeenCalledWith([issue("warning")]);
  });

  it("asks for confirmation with the error count and does not deploy when cancelled", async () => {
    validation.mockResolvedValue([issue("error"), issue("error"), issue("warning")]);
    renderBar();
    fireEvent.click(await screen.findByTestId("deploy-bar-deploy"));
    const dialog = await screen.findByTestId("confirm-dialog");
    expect(dialog).toHaveTextContent(i18n.t("admin.processDesigner.deploy.confirm", { count: 2 }));
    expect(dialog).toHaveTextContent("2");
    fireEvent.click(within(dialog).getByRole("button", { name: i18n.t("common.cancel") }));
    await new Promise((r) => setTimeout(r, 30));
    expect(deploy).not.toHaveBeenCalled();
  });

  it("deploys after the error confirmation", async () => {
    validation.mockResolvedValue([issue("error")]);
    renderBar();
    fireEvent.click(await screen.findByTestId("deploy-bar-deploy"));
    const dialog = await screen.findByTestId("confirm-dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: i18n.t("admin.deploy.deployNow") }));
    await waitFor(() => expect(deploy).toHaveBeenCalledWith("process"));
  });

  it("does not deploy when the check itself fails", async () => {
    validation.mockRejectedValue(new Error("boom"));
    renderBar();
    fireEvent.click(await screen.findByTestId("deploy-bar-deploy"));
    expect(await screen.findByTestId("deploy-bar-error")).toBeInTheDocument();
    expect(deploy).not.toHaveBeenCalled();
  });

  it("is disabled while there are unsaved edits", async () => {
    renderBar({ disabled: true });
    expect(await screen.findByTestId("deploy-bar-deploy")).toBeDisabled();
  });

  it("shows the Znuny state and no applied text while pending", async () => {
    status.mockResolvedValue(
      body(kind({ deploy_mode: "signal", znuny_sync: { state: "pending", revision_id: 9, message: "", since: null, overdue: false } })),
    );
    renderBar();
    expect(await screen.findByTestId("deploy-sync-pending")).toBeInTheDocument();
    expect(screen.queryByTestId("deploy-sync-ok")).not.toBeInTheDocument();
  });
});
