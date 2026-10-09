import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { LinksTab } from "./LinksTab";
import { makeProvider } from "./testFixtures";

const links = vi.fn();
const removeLink = vi.fn();

vi.mock("@/lib/identityApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/identityApi")>();
  return {
    ...actual,
    identityApi: {
      links: (...a: unknown[]) => links(...a),
      removeLink: (...a: unknown[]) => removeLink(...a),
    },
  };
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

const rows = [
  { id: 5, user_kind: "agent", subject: "subject-agent-1", user_id: 2, user_login: "idp.anna", first_seen: "2026-09-20T07:15:00Z", last_seen: "2026-10-08T06:45:00Z" },
  { id: 6, user_kind: "customer", subject: "subject-cust-1", user_id: 14, user_login: "kunde@example.org", first_seen: "2026-10-02T12:00:00Z", last_seen: "2026-10-07T16:20:00Z" },
];

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("de");
  links.mockResolvedValue(rows);
});

describe("LinksTab", () => {
  it("lists account, kind and subject", async () => {
    wrap(<LinksTab provider={makeProvider()} />);
    const list = await screen.findAllByTestId("identity-link-row");
    expect(list).toHaveLength(2);
    expect(list[0]).toHaveTextContent("idp.anna");
    expect(list[0]).toHaveTextContent("Agent");
    expect(list[0]).toHaveTextContent("subject-agent-1");
    expect(list[1]).toHaveTextContent("Kunde");
    expect(links).toHaveBeenCalledWith(1, expect.anything());
  });

  it("removes a link only after confirmation", async () => {
    removeLink.mockResolvedValue(undefined);
    wrap(<LinksTab provider={makeProvider()} />);
    fireEvent.click(await screen.findByTestId("identity-link-remove-5"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-cancel"));
    expect(removeLink).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("identity-link-remove-5"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(removeLink).toHaveBeenCalledWith(1, 5));
  });

  it("shows an empty state and a removal error", async () => {
    links.mockResolvedValue([]);
    const { unmount } = wrap(<LinksTab provider={makeProvider()} />);
    expect(await screen.findByTestId("identity-links-empty")).toBeInTheDocument();
    unmount();
    links.mockResolvedValue(rows);
    removeLink.mockRejectedValue(new ApiError(500, "boom", "/x"));
    wrap(<LinksTab provider={makeProvider()} />);
    fireEvent.click(await screen.findByTestId("identity-link-remove-5"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    expect(await screen.findByTestId("identity-link-remove-error")).toBeInTheDocument();
  });
});
