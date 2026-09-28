import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { ArticleListItem, TelegramMessageMeta } from "@/lib/api";
import {
  TelegramBubbleActions,
  TelegramButtonPills,
  TelegramEditForm,
  TelegramQuote,
  TelegramRetractConfirm,
  TelegramStatusLine,
} from "./TelegramBubbleExtras";

const { getArticleBody } = vi.hoisted(() => ({ getArticleBody: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { getArticleBody } };
});

function meta(over: Partial<TelegramMessageMeta> = {}): TelegramMessageMeta {
  return {
    article_id: 2,
    direction: "out",
    reply_to_article_id: null,
    buttons: [],
    answered_button: null,
    edited_at: null,
    retracted_at: null,
    ...over,
  };
}

const quoted = {
  id: 1,
  ticket_id: 7,
  sender_type: "customer",
  communication_channel_id: 9,
  communication_channel_name: "Telegram",
  from_address: "Jona M.",
  create_time: "2026-09-26T14:01:00Z",
  is_visible_for_customer: true,
} as ArticleListItem;

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("Telegram bubble extras", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("de");
  });
  beforeEach(() => {
    getArticleBody.mockReset();
  });

  it("quotes the first 80 characters of the replied-to message", async () => {
    getArticleBody.mockResolvedValue({
      article_id: 1,
      content_type: "text/plain",
      is_html: false,
      body: "x".repeat(79) + "yz and more text after the cut",
    });
    wrap(<TelegramQuote ticketId={7} quotedId={1} quoted={quoted} />);
    expect(screen.getByTestId("telegram-quote-1")).toHaveTextContent("Antwort auf Jona M.");
    const snippet = await screen.findByTestId("telegram-quote-snippet");
    expect(snippet.textContent).toBe("x".repeat(79) + "y…");
    expect(getArticleBody).toHaveBeenCalledWith(7, 1);
  });

  it("renders answer buttons as pills and highlights the answered one", () => {
    wrap(
      <TelegramButtonPills
        buttons={[
          { label: "Ja, geht wieder", action: "resolve_yes" },
          { label: "Nein", action: "resolve_no" },
        ]}
        answered={1}
      />,
    );
    const pills = screen.getAllByTestId(/^telegram-button-/);
    expect(pills.map((p) => p.textContent)).toEqual(["Ja, geht wieder", "Nein"]);
    expect(pills[0]).not.toHaveAttribute("data-answered");
    expect(pills[1]).toHaveAttribute("data-answered", "true");
  });

  it("shows edited label and a single delivery tick for delivered outbound messages", () => {
    wrap(<TelegramStatusLine meta={meta({ edited_at: "2026-09-26T14:05:00Z" })} />);
    expect(screen.getByText("bearbeitet")).toBeInTheDocument();
    expect(screen.getByTestId("telegram-delivered")).toHaveTextContent(/^✓$/);
  });

  it("shows retracted instead of a tick, and no tick for inbound messages", () => {
    const { rerender } = wrap(
      <TelegramStatusLine meta={meta({ retracted_at: "2026-09-26T14:06:00Z" })} />,
    );
    expect(screen.getByText("zurückgezogen")).toBeInTheDocument();
    expect(screen.queryByTestId("telegram-delivered")).toBeNull();
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <I18nextProvider i18n={i18n}>
          <TelegramStatusLine meta={meta({ direction: "in" })} />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId("telegram-delivered")).toBeNull();
  });

  it("offers quote always, edit and retract only when modifiable", () => {
    const onQuote = vi.fn();
    const onEdit = vi.fn();
    const onRetract = vi.fn();
    const { rerender } = wrap(
      <TelegramBubbleActions
        articleId={2}
        canModify={false}
        onQuote={onQuote}
        onEdit={onEdit}
        onRetract={onRetract}
      />,
    );
    expect(screen.queryByRole("button", { name: "Bearbeiten" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Zurückziehen" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Zitieren" }));
    expect(onQuote).toHaveBeenCalled();

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <I18nextProvider i18n={i18n}>
          <TelegramBubbleActions
            articleId={2}
            canModify
            onQuote={onQuote}
            onEdit={onEdit}
            onRetract={onRetract}
          />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    fireEvent.click(screen.getByRole("button", { name: "Zurückziehen" }));
    expect(onEdit).toHaveBeenCalled();
    expect(onRetract).toHaveBeenCalled();
  });

  it("edit form saves the changed text and refuses an unchanged or empty one", () => {
    const onSave = vi.fn();
    const onCancel = vi.fn();
    wrap(<TelegramEditForm initial="Hallo" pending={false} onSave={onSave} onCancel={onCancel} />);
    const save = screen.getByRole("button", { name: "Speichern" });
    expect(save).toBeDisabled();
    const box = screen.getByRole("textbox");
    fireEvent.change(box, { target: { value: "   " } });
    expect(save).toBeDisabled();
    fireEvent.change(box, { target: { value: "Hallo Jona" } });
    fireEvent.click(save);
    expect(onSave).toHaveBeenCalledWith("Hallo Jona");
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("asks inline before retracting", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    wrap(<TelegramRetractConfirm pending={false} onConfirm={onConfirm} onCancel={onCancel} />);
    expect(screen.getByText("Wirklich zurückziehen?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ja" }));
    expect(onConfirm).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(onCancel).toHaveBeenCalled();
  });
});
