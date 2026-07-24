import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { StatesPage } from "./StatesPage";

const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const deactivate = vi.fn();
const request = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    adminStates: {
      list: (...args: unknown[]) => list(...args),
      create: (...args: unknown[]) => create(...args),
      update: (...args: unknown[]) => update(...args),
      deactivate: (...args: unknown[]) => deactivate(...args),
    },
    request: (...args: unknown[]) => request(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <StatesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleState = {
  id: 5,
  name: "open-tickets",
  comments: "Ticket is open",
  type_id: 2,
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("StatesPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();
    request.mockReset();

    list.mockResolvedValue({
      items: [sampleState],
      total: 1,
      page: 1,
      page_size: 25,
    });
    request.mockResolvedValue([
      { id: 1, name: "new" },
      { id: 2, name: "open" },
      { id: 3, name: "closed" },
    ]);
    create.mockResolvedValue({ ...sampleState, id: 6, name: "pending reminder" });
  });

  it("renders the state list with the resolved type name", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("open-tickets")).toBeInTheDocument();
    });
    // type_id 2 resolves to the "open" state-type name from the request() query.
    expect(screen.getByText("open")).toBeInTheDocument();
    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("GET", "/api/v1/admin/state-types");
    });
  });

  it("opens the edit drawer via the row menu with fields populated", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("open-tickets")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-5"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-5"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-form-name")).toHaveValue("open-tickets");
    expect(screen.getByTestId("admin-form-comments")).toHaveValue("Ticket is open");

    const typeField = screen.getByTestId("admin-form-type_id");
    expect(typeField.tagName).toBe("BUTTON");
    fireEvent.click(typeField);
    const panel = screen.getByTestId("admin-form-type_id-menu");
    expect(within(panel).getByText("open")).toBeInTheDocument();
    expect(within(panel).getByText("closed")).toBeInTheDocument();
  });

  it("submits a create with the mapped body", async () => {
    renderPage();
    await waitFor(() => expect(list).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("admin-new-button"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-form")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-form-name"), {
      target: { value: "pending reminder" },
    });

    fireEvent.click(screen.getByTestId("admin-form-type_id"));
    fireEvent.click(screen.getByText("closed"));

    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledTimes(1);
    });
    expect(create).toHaveBeenCalledWith({
      name: "pending reminder",
      type_id: 3,
      comments: null,
      valid_id: 1,
    });
  });

  it("deactivates a state via the row menu", async () => {
    deactivate.mockResolvedValue(undefined);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("open-tickets")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-5"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-5"));

    await waitFor(() => {
      expect(deactivate).toHaveBeenCalledWith(5);
    });
  });
});
