import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { AclPage } from "./AclPage";

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    to,
    params,
    children,
    ...rest
  }: {
    to: string;
    params?: Record<string, string>;
    children: ReactNode;
    [k: string]: unknown;
  }) => {
    const href = params
      ? Object.entries(params).reduce((acc, [key, value]) => acc.replace(`$${key}`, value), to)
      : to;
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    );
  },
}));

const listAcls = vi.fn();
const createAcl = vi.fn();
const deployStatus = vi.fn();

vi.mock("@/lib/deployStatusApi", () => ({
  deployStatusApi: { status: (...args: unknown[]) => deployStatus(...args), deploy: vi.fn() },
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    listAcls: (...args: unknown[]) => listAcls(...args),
    createAcl: (...args: unknown[]) => createAcl(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AclPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleAcl = {
  id: 2,
  name: "RestrictPriority",
  comments: null,
  description: "Restricts priority selection for non-admins.",
  valid_id: 1,
  stop_after_match: 1,
  config_match: "Properties:\n  Ticket:\n    Queue: Support",
  config_change: "Possible:\n  Ticket:\n    Priority: [normal]",
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

const deployKind = (pending: number) => ({
  pending_changes: pending,
  running_live: false,
  revision: { id: 3, source: "znuny", entity_count: 4, deployed_at: "2026-10-08T10:00:00Z", deployed_by: null },
  znuny_sync: { state: "not_needed", revision_id: null, message: "", since: null, overdue: false },
  deploy_mode: null,
});
const deployBody = (aclPending = 2) => ({
  process: deployKind(0),
  acl: deployKind(aclPending),
  ownership_active: false,
  can_deploy_from_tiqora: false,
});

describe("AclPage", () => {
  beforeEach(() => {
    listAcls.mockReset();
    createAcl.mockReset();
    deployStatus.mockReset();
    deployStatus.mockResolvedValue(deployBody());
  });

  it("lists ACLs with a link to their detail page", async () => {
    listAcls.mockResolvedValue([sampleAcl]);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("RestrictPriority")).toBeInTheDocument();
    });
    expect(screen.getByText("Restricts priority selection for non-admins.")).toBeInTheDocument();

    const link = screen.getByTestId("acl-link-2");
    expect(link).toHaveAttribute("href", "/admin/acl/2");
  });

  it("shows a placeholder when the ACL has no description", async () => {
    listAcls.mockResolvedValue([{ ...sampleAcl, id: 3, name: "NoDescription", description: null }]);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("NoDescription")).toBeInTheDocument();
    });
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows an empty table when there are no ACLs", async () => {
    listAcls.mockResolvedValue([]);
    renderPage();

    await waitFor(() => {
      expect(listAcls).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("acl-link-2")).not.toBeInTheDocument();
  });

  it("offers a create button without a read-only badge", async () => {
    listAcls.mockResolvedValue([]);
    renderPage();

    await waitFor(() => {
      expect(listAcls).toHaveBeenCalled();
    });
    expect(screen.getByTestId("admin-acl-new")).toBeInTheDocument();
    expect(screen.queryByText(/read-?only/i)).not.toBeInTheDocument();
  });

  it("creates an ACL from the drawer", async () => {
    listAcls.mockResolvedValue([]);
    createAcl.mockResolvedValue({ ...sampleAcl, id: 9, name: "NewAcl" });
    renderPage();

    await waitFor(() => expect(listAcls).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("admin-acl-new"));

    const nameInput = await screen.findByTestId("admin-acl-name");
    fireEvent.change(nameInput, { target: { value: "NewAcl" } });
    fireEvent.click(screen.getByTestId("admin-acl-submit"));

    await waitFor(() => {
      expect(createAcl).toHaveBeenCalled();
    });
    const body = createAcl.mock.calls[0][0];
    expect(body.name).toBe("NewAcl");
    // the new draft shows up in the deploy card on the same page
    await waitFor(() => expect(deployStatus).toHaveBeenCalledTimes(2));
  });

  it("shows the ACL deploy status with the number of undeployed edits", async () => {
    listAcls.mockResolvedValue([sampleAcl]);
    renderPage();

    const card = await screen.findByTestId("deploy-status-acl");
    expect(card).toBeInTheDocument();
    expect(screen.getByTestId("deploy-status-pending")).toHaveTextContent(
      i18n.t("admin.deploy.pending", { count: 2 }),
    );
    expect(screen.queryByTestId("deploy-status-process")).not.toBeInTheDocument();
  });
});
