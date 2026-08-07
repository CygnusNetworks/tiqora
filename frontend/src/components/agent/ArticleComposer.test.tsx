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
    // The minutes/hours toggle persists to localStorage — start each test
    // from the "min" default rather than leaking a prior test's choice.
    window.localStorage.clear();
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
    // Minutes mode rounds to the nearest whole minute — no fractional bookings.
    fireEvent.change(screen.getByTestId("composer-time"), { target: { value: "7.5" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() =>
      expect(createTicketTimeAccounting).toHaveBeenCalledWith(7, { time_unit: 8 }),
    );
  });

  it("fills the time chip from a preset", async () => {
    wrap();
    fireEvent.change(screen.getByTestId("composer-body"), { target: { value: "Notiz" } });
    fireEvent.click(screen.getByTestId("composer-time-presets-trigger"));
    fireEvent.click(await screen.findByTestId("composer-time-preset-15"));
    expect(screen.getByTestId("composer-time")).toHaveValue(15);
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() =>
      expect(createTicketTimeAccounting).toHaveBeenCalledWith(7, { time_unit: 15 }),
    );
  });

  it("books hours converted to whole minutes when Std mode is selected", async () => {
    wrap();
    fireEvent.change(screen.getByTestId("composer-body"), { target: { value: "Notiz" } });
    fireEvent.click(screen.getByTestId("composer-time-mode-hours"));
    fireEvent.change(screen.getByTestId("composer-time"), { target: { value: "0.5" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() =>
      expect(createTicketTimeAccounting).toHaveBeenCalledWith(7, { time_unit: 30 }),
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
