import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { phoneApi } from "@/lib/phoneApi";
import { DialPage } from "./DialPage";

let search: { number: string; ticket?: number } = { number: "+491717630944", ticket: 42 };

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => search,
  Link: ({ children, ...rest }: { children: React.ReactNode } & Record<string, unknown>) => (
    <a data-to={String(rest.to)}>{children}</a>
  ),
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <DialPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  search = { number: "+491717630944", ticket: 42 };
  vi.spyOn(phoneApi, "phoneConfig").mockResolvedValue({ dial_scheme: "tel", originate: true });
  vi.spyOn(phoneApi, "callerLookup").mockResolvedValue({
    number_normalized: "491717630944",
    customers: [],
    open_tickets: [],
  });
});

describe("DialPage", () => {
  it("does not dial on load, dials on click", async () => {
    const dialSpy = vi.spyOn(phoneApi, "dial").mockResolvedValue({ extension: "60", number: "01717630944" });
    renderPage();
    const button = await screen.findByTestId("dial-page-call");
    expect(dialSpy).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(dialSpy).toHaveBeenCalledWith({ number: "+491717630944", ticket_id: 42, name: "" });
    expect(await screen.findByTestId("dial-page-status")).toBeInTheDocument();
  });

  it("shows the server's error message", async () => {
    vi.spyOn(phoneApi, "dial").mockRejectedValue(new ApiError(409, "No extension assigned", "/api/v1/phone/dial"));
    renderPage();
    fireEvent.click(await screen.findByTestId("dial-page-call"));
    expect(await screen.findByRole("alert")).toHaveTextContent("No extension assigned");
  });

  it("falls back to a tel: link when click-to-dial is off", async () => {
    vi.spyOn(phoneApi, "phoneConfig").mockResolvedValue({ dial_scheme: "tel", originate: false });
    renderPage();
    const link = await screen.findByRole("link", { name: "Call with the phone app" });
    expect(link).toHaveAttribute("href", "tel:+491717630944");
    expect(screen.queryByTestId("dial-page-call")).toBeNull();
  });

  it("offers neither call button nor tel: link without a number", async () => {
    search = { number: "" };
    renderPage();
    expect(await screen.findByText("–")).toBeInTheDocument();
    expect(screen.queryByTestId("dial-page-call")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
