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
  znuny_sync: { state: "not_needed", revision_id: null, message: "", since: null, overdue: false },
  deploy_mode: null,
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

function renderCard(k: "process" | "acl" = "process", props: Partial<React.ComponentProps<typeof DeployStatusCard>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <DeployStatusCard kind={k} {...props} />
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

  it("offers Bereitstellen only with a deploy_mode of that kind and pending changes, and posts the kind", async () => {
    status.mockResolvedValue(
      body({ can_deploy_from_tiqora: true, acl: kind({ pending_changes: 2, deploy_mode: "local" }) }),
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
    status.mockResolvedValue(
      body({ can_deploy_from_tiqora: true, acl: kind({ pending_changes: 2, deploy_mode: "local" }) }),
    );
    const { ApiError } = await import("@/lib/api");
    deploy.mockRejectedValue(new ApiError(422, { detail: [{ msg: "Unknown kind" }] }, "/x"));
    renderCard("acl");
    fireEvent.click(await screen.findByTestId("deploy-status-deploy"));
    const err = await screen.findByTestId("deploy-status-error");
    expect(err).toHaveTextContent("Unknown kind");
    expect(err).not.toHaveTextContent("object Object");
  });

  it("offers no deploy button for a kind without deploy_mode, even if another kind may deploy", async () => {
    status.mockResolvedValue(
      body({
        can_deploy_from_tiqora: true,
        process: kind({ pending_changes: 1, deploy_mode: "signal" }),
        acl: kind({ pending_changes: 2, deploy_mode: null }),
      }),
    );
    renderCard("acl");
    await screen.findByTestId("deploy-status-pending");
    expect(screen.queryByTestId("deploy-status-deploy")).not.toBeInTheDocument();
  });

  it("hides the deploy button when nothing is pending, even if Tiqora may deploy", async () => {
    status.mockResolvedValue(body({ can_deploy_from_tiqora: true, process: kind({ deploy_mode: "local" }) }));
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

  describe("Znuny sync state", () => {
    const sync = (over: Partial<DeployStatus["process"]["znuny_sync"]>) => ({
      state: "pending" as const,
      revision_id: 8,
      message: "",
      since: "2026-10-12T10:00:00Z",
      overdue: false,
      ...over,
    });

    it("never says the change was applied while it is pending", async () => {
      status.mockResolvedValue(body({ process: kind({ deploy_mode: "signal", znuny_sync: sync({}) }) }));
      renderCard();
      expect(await screen.findByTestId("deploy-sync-pending")).toHaveTextContent(i18n.t("admin.deploy.sync.pending"));
      expect(screen.queryByTestId("deploy-sync-ok")).not.toBeInTheDocument();
      expect(screen.queryByText(i18n.t("admin.deploy.sync.ok"))).not.toBeInTheDocument();
    });

    it("says the change was applied once Znuny reports ok", async () => {
      status.mockResolvedValue(body({ process: kind({ deploy_mode: "signal", znuny_sync: sync({ state: "ok" }) }) }));
      renderCard();
      expect(await screen.findByTestId("deploy-sync-ok")).toHaveTextContent(i18n.t("admin.deploy.sync.ok"));
      expect(screen.queryByTestId("deploy-sync-pending")).not.toBeInTheDocument();
    });

    it("explains a missing add-on", async () => {
      status.mockResolvedValue(
        body({ process: kind({ deploy_mode: "signal", znuny_sync: sync({ state: "addon_missing" }) }) }),
      );
      renderCard();
      expect(await screen.findByTestId("deploy-sync-addon-missing")).toHaveTextContent("TiqoraSync");
    });

    it("shows an error prominently with the server message and the old-version hint", async () => {
      status.mockResolvedValue(
        body({ process: kind({ deploy_mode: "signal", znuny_sync: sync({ state: "error", message: "boom in Znuny" }) }) }),
      );
      renderCard();
      const err = await screen.findByTestId("deploy-sync-error");
      expect(err).toHaveAttribute("role", "alert");
      expect(err).toHaveTextContent("boom in Znuny");
      expect(err).toHaveTextContent(i18n.t("admin.deploy.sync.errorHint"));
    });

    it("polls while pending, shows ok when Znuny catches up and stops polling", async () => {
      status
        .mockResolvedValueOnce(body({ process: kind({ deploy_mode: "signal", znuny_sync: sync({}) }) }))
        .mockResolvedValue(body({ process: kind({ deploy_mode: "signal", znuny_sync: sync({ state: "ok" }) }) }));
      renderCard("process", { pollMs: 20 });
      await screen.findByTestId("deploy-sync-pending");
      expect(await screen.findByTestId("deploy-sync-ok", {}, { timeout: 3000 })).toBeInTheDocument();
      const calls = status.mock.calls.length;
      await new Promise((r) => setTimeout(r, 120));
      expect(status.mock.calls.length).toBe(calls);
    });

    it("gives up polling after the limit and shows the overdue text", async () => {
      status.mockResolvedValue(body({ process: kind({ deploy_mode: "signal", znuny_sync: sync({}) }) }));
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={qc}>
          <I18nextProvider i18n={i18n}>
            <DeployStatusCard kind="process" pollMs={20} giveUpMs={120} />
          </I18nextProvider>
        </QueryClientProvider>,
      );
      expect(await screen.findByTestId("deploy-sync-overdue", {}, { timeout: 3000 })).toHaveTextContent(
        i18n.t("admin.deploy.sync.overdue"),
      );
      const calls = status.mock.calls.length;
      expect(calls).toBeGreaterThan(1);
      await new Promise((r) => setTimeout(r, 100));
      expect(status.mock.calls.length).toBe(calls);
    });

    it("starts polling again when a redeploy begins a new pending period after giving up", async () => {
      status.mockResolvedValue(body({ process: kind({ deploy_mode: "signal", znuny_sync: sync({}) }) }));
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={qc}>
          <I18nextProvider i18n={i18n}>
            <DeployStatusCard kind="process" pollMs={20} giveUpMs={120} />
          </I18nextProvider>
        </QueryClientProvider>,
      );
      await screen.findByTestId("deploy-sync-overdue", {}, { timeout: 3000 });
      // The admin deploys again: still "pending", but for a new request (new `since`).
      status
        .mockResolvedValueOnce(
          body({ process: kind({ deploy_mode: "signal", znuny_sync: sync({ since: "2026-10-12T10:30:00Z" }) }) }),
        )
        .mockResolvedValue(body({ process: kind({ deploy_mode: "signal", znuny_sync: sync({ state: "ok" }) }) }));
      await qc.invalidateQueries({ queryKey: ["admin", "deploy-status"] });
      expect(await screen.findByTestId("deploy-sync-ok", {}, { timeout: 3000 })).toBeInTheDocument();
    });

    it("hides the card's own deploy button when a parent owns it", async () => {
      status.mockResolvedValue(body({ process: kind({ deploy_mode: "local", pending_changes: 2 }) }));
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={qc}>
          <I18nextProvider i18n={i18n}>
            <DeployStatusCard kind="process" showDeploy={false} />
          </I18nextProvider>
        </QueryClientProvider>,
      );
      await screen.findByTestId("deploy-status-pending");
      expect(screen.queryByTestId("deploy-status-deploy")).not.toBeInTheDocument();
    });
  });
});
