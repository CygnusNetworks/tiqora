import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { RolesPage } from "./RolesPage";

const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const deactivate = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    adminRoles: {
      list: (...args: unknown[]) => list(...args),
      create: (...args: unknown[]) => create(...args),
      update: (...args: unknown[]) => update(...args),
      deactivate: (...args: unknown[]) => deactivate(...args),
    },
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RolesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleRole = {
  id: 3,
  name: "agent",
  comments: "Standard agent role",
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("RolesPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();

    list.mockResolvedValue({
      items: [sampleRole],
      total: 1,
      page: 1,
      page_size: 25,
    });
    create.mockResolvedValue({ ...sampleRole, id: 4, name: "supervisor" });
    update.mockResolvedValue({ ...sampleRole, name: "agent-updated" });
  });

  it("renders the role list with resolved data", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("agent")).toBeInTheDocument();
    });
    expect(screen.getByText("Standard agent role")).toBeInTheDocument();
    expect(list).toHaveBeenCalled();
  });

  it("opens the edit drawer via the row menu with fields populated", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("agent")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-3"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-3"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-form-name")).toHaveValue("agent");
    expect(screen.getByTestId("admin-form-comments")).toHaveValue("Standard agent role");

    const validField = screen.getByTestId("admin-form-valid_id");
    expect(validField.tagName).toBe("BUTTON");
    fireEvent.click(validField);
    const panel = screen.getByTestId("admin-form-valid_id-menu");
    expect(within(panel).getByText("Valid")).toBeInTheDocument();
  });

  it("submits a create with the mapped body", async () => {
    renderPage();
    await waitFor(() => expect(list).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("admin-new-button"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-form")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-form-name"), {
      target: { value: "supervisor" },
    });
    fireEvent.change(screen.getByTestId("admin-form-comments"), {
      target: { value: "Escalation role" },
    });

    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledTimes(1);
    });
    expect(create).toHaveBeenCalledWith({
      name: "supervisor",
      comments: "Escalation role",
      valid_id: 1,
    });
  });

  it("deactivates a role via the row menu", async () => {
    deactivate.mockResolvedValue(undefined);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("agent")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-3"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-3"));

    await waitFor(() => {
      expect(deactivate).toHaveBeenCalledWith(3);
    });
  });
});
