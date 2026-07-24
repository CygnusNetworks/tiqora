import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { GroupsPage } from "./GroupsPage";

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
    adminGroups: {
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
        <GroupsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleGroup = {
  id: 3,
  name: "admin",
  comments: "Administrators",
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("GroupsPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();

    list.mockResolvedValue({
      items: [sampleGroup],
      total: 1,
      page: 1,
      page_size: 25,
    });
    create.mockResolvedValue({ ...sampleGroup, id: 4, name: "editors" });
    update.mockResolvedValue(sampleGroup);
    deactivate.mockResolvedValue(undefined);
  });

  it("renders the list with resolved data", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("admin")).toBeInTheDocument();
    });
    expect(screen.getByText("Administrators")).toBeInTheDocument();
  });

  it("opens the edit drawer via the row menu with fields populated", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("admin")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-3"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-3"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-form-name")).toHaveValue("admin");
    expect(screen.getByTestId("admin-form-comments")).toHaveValue("Administrators");
  });

  it("submits a create with the entered field values", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("admin")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-new-button"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-name")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-form-name"), { target: { value: "editors" } });
    fireEvent.change(screen.getByTestId("admin-form-comments"), {
      target: { value: "Content editors" },
    });

    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith({
        name: "editors",
        comments: "Content editors",
        valid_id: 1,
      });
    });
  });

  it("deactivates a row via the row menu", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("admin")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-3"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-3"));

    await waitFor(() => {
      expect(deactivate).toHaveBeenCalledWith(3);
    });
  });
});
