import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    listAcls: (...args: unknown[]) => listAcls(...args),
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

describe("AclPage", () => {
  beforeEach(() => {
    listAcls.mockReset();
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
});
