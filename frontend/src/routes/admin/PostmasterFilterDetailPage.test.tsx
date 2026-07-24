import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { PostmasterFilterDetailPage } from "./PostmasterFilterDetailPage";

const getPostmasterFilter = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ name: "spam-to-junk" }),
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode; [k: string]: unknown }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", () => ({
  api: {
    getPostmasterFilter: (...args: unknown[]) => getPostmasterFilter(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PostmasterFilterDetailPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("PostmasterFilterDetailPage", () => {
  beforeEach(() => {
    getPostmasterFilter.mockReset();
    getPostmasterFilter.mockResolvedValue({
      name: "spam-to-junk",
      rules: [
        {
          f_name: "spam-to-junk",
          f_stop: 1,
          f_type: "Match",
          f_key: "X-Spam-Flag",
          f_value: "Yes",
          f_not: 1,
        },
        {
          f_name: "spam-to-junk",
          f_stop: 1,
          f_type: "Set",
          f_key: "X-OTRS-Queue",
          f_value: "Junk",
          f_not: 0,
        },
      ],
    });
  });

  it("fetches the named filter and renders its rules", async () => {
    renderPage();

    await waitFor(() => {
      expect(getPostmasterFilter).toHaveBeenCalledWith("spam-to-junk", expect.anything());
    });
    expect(screen.getByText("spam-to-junk")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("X-Spam-Flag")).toBeInTheDocument();
    });
    expect(screen.getByText("X-OTRS-Queue")).toBeInTheDocument();
    expect(screen.getByText("Junk")).toBeInTheDocument();
  });

  it("shows a negated match value with the ≠ marker and stop checkmarks", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/≠ Yes/)).toBeInTheDocument();
    });
    const checkmarks = screen.getAllByText("✓");
    expect(checkmarks.length).toBe(2);
  });

  it("renders an empty rules table when the filter has no rules", async () => {
    getPostmasterFilter.mockResolvedValue({ name: "empty-filter", rules: [] });
    renderPage();

    await waitFor(() => {
      expect(getPostmasterFilter).toHaveBeenCalled();
    });
    expect(screen.queryByText("X-Spam-Flag")).not.toBeInTheDocument();
  });
});
