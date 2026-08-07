import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ArticleComposer } from "./ArticleTimeline";

const {
  createArticle,
  createTicketMention,
  createTicketTimeAccounting,
  listReferenceAgents,
} = vi.hoisted(() => ({
  createArticle: vi.fn(),
  createTicketMention: vi.fn(),
  createTicketTimeAccounting: vi.fn(),
  listReferenceAgents: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: { createArticle, createTicketMention, createTicketTimeAccounting, listReferenceAgents },
  };
});

function wrap() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <ArticleComposer ticketId={7} articles={[]} open onOpenChange={vi.fn()} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("ArticleComposer extras", () => {
  beforeEach(() => {
    createArticle.mockReset().mockResolvedValue({ id: 42 });
    createTicketMention.mockReset().mockResolvedValue({ id: 1 });
    createTicketTimeAccounting.mockReset().mockResolvedValue({ id: 1 });
    listReferenceAgents
      .mockReset()
      .mockResolvedValue([{ id: 3, login: "bob", full_name: "Bob Stone" }]);
  });

  it("saves a plain note without booking anything", async () => {
    wrap();
    fireEvent.change(screen.getByTestId("composer-body"), { target: { value: "Notiz" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(createArticle).toHaveBeenCalled());
    expect(createTicketMention).not.toHaveBeenCalled();
    expect(createTicketTimeAccounting).not.toHaveBeenCalled();
  });

  it("mentions a colleague picked from the @ typeahead", async () => {
    wrap();
    const body = screen.getByTestId("composer-body") as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "@bo" } });
    body.setSelectionRange(3, 3);
    fireEvent.keyUp(body, { key: "o" });
    fireEvent.mouseDown(await screen.findByTestId("mention-option-3"));
    await waitFor(() => expect(body.value).toContain("@Bob Stone"));
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(createTicketMention).toHaveBeenCalledWith(7, { user_id: 3 }));
  });

  it("books the minutes from the footer chip", async () => {
    wrap();
    fireEvent.change(screen.getByTestId("composer-body"), { target: { value: "Notiz" } });
    fireEvent.change(screen.getByTestId("composer-time"), { target: { value: "7.5" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() =>
      expect(createTicketTimeAccounting).toHaveBeenCalledWith(7, { time_unit: 7.5 }),
    );
  });

  it("keeps the note text and offers a retry when the booking fails", async () => {
    createTicketTimeAccounting.mockRejectedValueOnce(new Error("boom"));
    wrap();
    fireEvent.change(screen.getByTestId("composer-body"), { target: { value: "Notiz" } });
    fireEvent.change(screen.getByTestId("composer-time"), { target: { value: "5" } });
    fireEvent.click(screen.getByTestId("composer-send"));

    await screen.findByTestId("composer-extras-error");
    expect(screen.getByTestId("composer-body")).toHaveValue("Notiz");
    expect(screen.queryByTestId("composer-send")).toBeNull();

    fireEvent.click(screen.getByTestId("composer-extras-retry"));
    await waitFor(() => expect(createTicketTimeAccounting).toHaveBeenCalledTimes(2));
    // The note itself is never written twice.
    expect(createArticle).toHaveBeenCalledTimes(1);
  });
});
