import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { AclDetailPage } from "./AclDetailPage";

let currentAclId = "2";

vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ aclId: currentAclId }),
  useNavigate: () => vi.fn(),
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode; [k: string]: unknown }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

const getAcl = vi.fn();
const updateAcl = vi.fn();
const deleteAcl = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    getAcl: (...args: unknown[]) => getAcl(...args),
    updateAcl: (...args: unknown[]) => updateAcl(...args),
    deleteAcl: (...args: unknown[]) => deleteAcl(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AclDetailPage />
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

describe("AclDetailPage", () => {
  beforeEach(() => {
    currentAclId = "2";
    getAcl.mockReset();
    updateAcl.mockReset();
    deleteAcl.mockReset();
  });

  it("loads the ACL by id and renders editable match/change config", async () => {
    getAcl.mockResolvedValue(sampleAcl);
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("acl-detail-name")).toHaveValue("RestrictPriority");
    });
    expect(getAcl).toHaveBeenCalledWith(2, expect.anything());
    expect(screen.getByTestId("acl-detail-config-match")).toHaveValue(
      "Properties:\n  Ticket:\n    Queue: Support",
    );
    expect(screen.getByTestId("acl-detail-config-change")).toHaveValue(
      "Possible:\n  Ticket:\n    Priority: [normal]",
    );
    expect(screen.getByTestId("acl-detail-stop-after-match")).toBeChecked();
  });

  it("saves edits via updateAcl", async () => {
    getAcl.mockResolvedValue(sampleAcl);
    updateAcl.mockResolvedValue({ ...sampleAcl, name: "Renamed" });
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("acl-detail-name")).toHaveValue("RestrictPriority");
    });
    fireEvent.change(screen.getByTestId("acl-detail-name"), { target: { value: "Renamed" } });
    fireEvent.click(screen.getByTestId("acl-detail-save"));

    await waitFor(() => {
      expect(updateAcl).toHaveBeenCalled();
    });
    expect(updateAcl.mock.calls[0][0]).toBe(2);
    expect(updateAcl.mock.calls[0][1].name).toBe("Renamed");
  });

  it("requests a different ACL when the route id changes", async () => {
    currentAclId = "9";
    getAcl.mockResolvedValue({ ...sampleAcl, id: 9, name: "Other ACL" });
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("acl-detail-name")).toHaveValue("Other ACL");
    });
    expect(getAcl).toHaveBeenCalledWith(9, expect.anything());
  });
});
