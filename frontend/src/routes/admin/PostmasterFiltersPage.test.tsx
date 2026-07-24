import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { PostmasterFiltersPage } from "./PostmasterFiltersPage";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode; [k: string]: unknown }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

const listPostmasterFilters = vi.fn();
const getPostmasterFilter = vi.fn();
const createPostmasterFilter = vi.fn();
const updatePostmasterFilter = vi.fn();
const deletePostmasterFilter = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    listPostmasterFilters: (...args: unknown[]) => listPostmasterFilters(...args),
    getPostmasterFilter: (...args: unknown[]) => getPostmasterFilter(...args),
    createPostmasterFilter: (...args: unknown[]) => createPostmasterFilter(...args),
    updatePostmasterFilter: (...args: unknown[]) => updatePostmasterFilter(...args),
    deletePostmasterFilter: (...args: unknown[]) => deletePostmasterFilter(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PostmasterFiltersPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleFilter = {
  name: "spam-to-junk",
  rules: [
    { f_name: "spam-to-junk", f_stop: 1, f_type: "Match", f_key: "X-Spam-Flag", f_value: "Yes", f_not: 0 },
    { f_name: "spam-to-junk", f_stop: 1, f_type: "Set", f_key: "X-OTRS-Queue", f_value: "Junk", f_not: 0 },
  ],
};

describe("PostmasterFiltersPage", () => {
  beforeEach(() => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    listPostmasterFilters.mockReset();
    getPostmasterFilter.mockReset();
    createPostmasterFilter.mockReset();
    updatePostmasterFilter.mockReset();
    deletePostmasterFilter.mockReset();

    listPostmasterFilters.mockResolvedValue([sampleFilter]);
    createPostmasterFilter.mockResolvedValue(sampleFilter);
    updatePostmasterFilter.mockResolvedValue(sampleFilter);
    deletePostmasterFilter.mockResolvedValue(undefined);
  });

  it("renders filter rows with match/set counts and stop badge", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("postmaster-filter-link-spam-to-junk")).toBeInTheDocument();
    });
    expect(listPostmasterFilters).toHaveBeenCalled();
    const row = screen.getByTestId("admin-row-spam-to-junk");
    expect(row).toHaveTextContent("1"); // match count
  });

  it("creates a new filter with a match rule", async () => {
    createPostmasterFilter.mockResolvedValue({
      name: "new-filter",
      rules: [
        { f_name: "new-filter", f_stop: null, f_type: "Match", f_key: "From", f_value: "spam@example.com", f_not: 0 },
      ],
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("postmaster-filter-link-spam-to-junk")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-postmaster-filters-new"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-postmaster-filters-form-name")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("admin-postmaster-filters-form-name"), {
      target: { value: "new-filter" },
    });
    fireEvent.change(screen.getByTestId("admin-pm-match-value-0"), {
      target: { value: "spam@example.com" },
    });
    fireEvent.click(screen.getByTestId("admin-postmaster-filters-form-submit"));

    await waitFor(() => {
      expect(createPostmasterFilter).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "new-filter",
          stop: false,
          match: [{ key: "From", value: "spam@example.com", negate: false }],
        }),
      );
    });
  });

  it("blocks submit and flags the field when the name is missing", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("postmaster-filter-link-spam-to-junk")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-postmaster-filters-new"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-pm-match-value-0")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("admin-pm-match-value-0"), {
      target: { value: "spam@example.com" },
    });
    fireEvent.click(screen.getByTestId("admin-postmaster-filters-form-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-postmaster-filters-form-name")).toHaveAttribute(
        "aria-invalid",
        "true",
      );
    });
    expect(createPostmasterFilter).not.toHaveBeenCalled();
  });

  it("shows a validation error when there is no match rule", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("postmaster-filter-link-spam-to-junk")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-postmaster-filters-new"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-postmaster-filters-form-name")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("admin-postmaster-filters-form-name"), {
      target: { value: "empty-match" },
    });
    // clear the only match row's value so it gets filtered out of the payload
    fireEvent.click(screen.getByTestId("admin-pm-match-remove-0"));

    // handleSubmit throws by design here (keeps the drawer open on
    // validation failure) but its promise is never awaited by the caller
    // (CrudDrawer does `void handleSubmit()`), so it surfaces as a Node
    // unhandledRejection under Vitest — swallow that expected rejection.
    const onUnhandledRejection = () => {};
    process.on("unhandledRejection", onUnhandledRejection);
    try {
      fireEvent.click(screen.getByTestId("admin-postmaster-filters-form-submit"));

      await waitFor(() => {
        expect(screen.getByTestId("admin-postmaster-filters-form-error")).toBeInTheDocument();
      });
      expect(createPostmasterFilter).not.toHaveBeenCalled();
    } finally {
      process.off("unhandledRejection", onUnhandledRejection);
    }
  });

  it("edits an existing filter and sends the updated payload", async () => {
    updatePostmasterFilter.mockResolvedValue({
      ...sampleFilter,
      rules: [
        { f_name: "spam-to-junk", f_stop: 1, f_type: "Match", f_key: "X-Spam-Flag", f_value: "Definitely", f_not: 0 },
        { f_name: "spam-to-junk", f_stop: 1, f_type: "Set", f_key: "X-OTRS-Queue", f_value: "Junk", f_not: 0 },
      ],
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("postmaster-filter-link-spam-to-junk")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-spam-to-junk"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-spam-to-junk"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-postmaster-filters-form-name")).toHaveValue("spam-to-junk");
    });
    expect(screen.getByTestId("admin-pm-match-value-0")).toHaveValue("Yes");

    fireEvent.change(screen.getByTestId("admin-pm-match-value-0"), {
      target: { value: "Definitely" },
    });
    fireEvent.click(screen.getByTestId("admin-postmaster-filters-form-submit"));

    await waitFor(() => {
      expect(updatePostmasterFilter).toHaveBeenCalledWith(
        "spam-to-junk",
        expect.objectContaining({
          name: "spam-to-junk",
          stop: true,
          match: [{ key: "X-Spam-Flag", value: "Definitely", negate: false }],
          set: [{ key: "X-OTRS-Queue", value: "Junk" }],
        }),
      );
    });
  });

  it("deletes a filter via the row menu and confirm dialog", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("postmaster-filter-link-spam-to-junk")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-spam-to-junk"));
    fireEvent.click(await screen.findByTestId("admin-row-deactivate-spam-to-junk"));

    await waitFor(() => {
      expect(screen.getByTestId("confirm-dialog")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => {
      expect(deletePostmasterFilter).toHaveBeenCalledWith("spam-to-junk");
    });
  });
});
