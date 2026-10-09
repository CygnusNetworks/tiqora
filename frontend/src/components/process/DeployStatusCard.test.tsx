import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { DeployStatusCard } from "./DeployStatusCard";
import type { DeployStatus } from "@/lib/deployStatusApi";

const { status, deploy } = vi.hoisted(() => ({ status: vi.fn(), deploy: vi.fn() }));

vi.mock("@/lib/deployStatusApi", () => ({ deployStatusApi: { status, deploy } }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      public detail: unknown,
    ) {
      super(`HTTP ${status}`);
    }
  },
}));

const kind = (over: Partial<DeployStatus["process"]> = {}): DeployStatus["process"] => ({
  pending_changes: 0,
  running_live: false,
  revision: {
    id: 7,
    source: "znuny",
    entity_count: 2,
    deployed_at: "2026-10-08T10:00:00Z",
    deployed_by: null,
  },
  ...over,
});

function body(over: Partial<DeployStatus> = {}): DeployStatus {
  return {
    process: kind(),
    acl: kind(),
    ownership_active: false,
    can_deploy_from_tiqora: false,
    ...over,
  };
}

function renderCard(k: "process" | "acl" = "process") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <DeployStatusCard kind={k} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("DeployStatusCard", () => {
  beforeEach(() => {
    status.mockReset();
    deploy.mockReset();
  });

  it("names the deployed revision and where it came from", async () => {
    status.mockResolvedValue(body());
    renderCard();
    const rev = await screen.findByTestId("deploy-status-revision");
    expect(rev).toHaveTextContent(i18n.t("admin.deploy.source.znuny"));
    expect(screen.getByTestId("deploy-status-process")).toBeInTheDocument();
    expect(screen.queryByTestId("deploy-status-pending")).not.toBeInTheDocument();
  });

  it("warns with the number of undeployed changes, without a deploy button when Tiqora may not deploy", async () => {
    status.mockResolvedValue(body({ process: kind({ pending_changes: 3 }) }));
    renderCard();
    const pending = await screen.findByTestId("deploy-status-pending");
    expect(pending).toHaveTextContent(i18n.t("admin.deploy.pending", { count: 3 }));
    expect(pending.textContent).toContain("3");
    expect(screen.queryByTestId("deploy-status-deploy")).not.toBeInTheDocument();
  });

  it("offers Bereitstellen only with can_deploy_from_tiqora and pending changes, and posts the kind", async () => {
    status.mockResolvedValue(
      body({ can_deploy_from_tiqora: true, acl: kind({ pending_changes: 2 }) }),
    );
    deploy.mockResolvedValue({ action: "recorded", checksum: "x", kind: "acl", revision_id: 8 });
    renderCard("acl");
    fireEvent.click(await screen.findByTestId("deploy-status-deploy"));
    await waitFor(() => expect(deploy).toHaveBeenCalledWith("acl"));
    expect(screen.getByTestId("deploy-status-acl")).toBeInTheDocument();
    // the card re-reads the status after a deploy
    await waitFor(() => expect(status).toHaveBeenCalledTimes(2));
  });

  it("shows the server's message for a failed deploy, never [object Object]", async () => {
    status.mockResolvedValue(body({ can_deploy_from_tiqora: true, acl: kind({ pending_changes: 2 }) }));
    const { ApiError } = await import("@/lib/api");
    deploy.mockRejectedValue(new ApiError(422, { detail: [{ msg: "Unknown kind" }] }, "/x"));
    renderCard("acl");
    fireEvent.click(await screen.findByTestId("deploy-status-deploy"));
    const err = await screen.findByTestId("deploy-status-error");
    expect(err).toHaveTextContent("Unknown kind");
    expect(err).not.toHaveTextContent("object Object");
  });

  it("hides the deploy button when nothing is pending, even if Tiqora may deploy", async () => {
    status.mockResolvedValue(body({ can_deploy_from_tiqora: true }));
    renderCard();
    await screen.findByTestId("deploy-status-revision");
    expect(screen.queryByTestId("deploy-status-deploy")).not.toBeInTheDocument();
  });

  it("shows a danger text when the engine still runs the drafts", async () => {
    status.mockResolvedValue(body({ process: kind({ running_live: true, revision: null }) }));
    renderCard();
    const live = await screen.findByTestId("deploy-status-running-live");
    expect(live).toHaveTextContent(i18n.t("admin.deploy.runningLive"));
    expect(live).toHaveClass("text-danger");
    expect(screen.queryByTestId("deploy-status-revision")).not.toBeInTheDocument();
  });

  it("renders nothing when the status cannot be loaded", async () => {
    status.mockRejectedValue(new Error("boom"));
    const { container } = renderCard();
    await waitFor(() => expect(status).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
