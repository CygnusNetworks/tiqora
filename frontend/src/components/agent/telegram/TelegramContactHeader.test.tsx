import { describe, it, expect, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { TelegramChatOut } from "@/lib/api";
import { TelegramContactHeader } from "./TelegramContactHeader";

function chat(over: Partial<TelegramChatOut> = {}): TelegramChatOut {
  return {
    chat_id: 5550001,
    username: "jona_m",
    display_name: "Jona M.",
    identity_verified: false,
    customer_user_login: null,
    consent_time: null,
    ai_escalated_at: null,
    messages: [],
    ...over,
  };
}

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("TelegramContactHeader", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("de");
  });

  it("shows name, @username and the unverified badge", () => {
    wrap(<TelegramContactHeader chat={chat()} locale="de" />);
    expect(screen.getByText("Jona M.")).toBeInTheDocument();
    expect(screen.getByText("@jona_m")).toBeInTheDocument();
    expect(screen.getByTestId("telegram-identity")).toHaveTextContent("Identität nicht bestätigt");
    expect(screen.queryByTestId("telegram-consent")).toBeNull();
    expect(screen.queryByTestId("telegram-ai-escalated")).toBeNull();
  });

  it("shows verified identity, consent and AI hand-over badges", () => {
    wrap(
      <TelegramContactHeader
        chat={chat({
          identity_verified: true,
          customer_user_login: "jona",
          consent_time: "2026-09-26T14:00:00Z",
          ai_escalated_at: "2026-09-26T14:04:00Z",
        })}
        locale="de"
      />,
    );
    expect(screen.getByTestId("telegram-identity")).toHaveTextContent("Identität bestätigt");
    expect(screen.getByTestId("telegram-identity")).not.toHaveTextContent("nicht");
    expect(screen.getByTestId("telegram-consent")).toHaveTextContent("Einwilligung erteilt");
    expect(screen.getByTestId("telegram-ai-escalated")).toHaveTextContent("KI an Team übergeben");
  });

  it("falls back to the username, then the chat id, when there is no display name", () => {
    const { rerender } = wrap(
      <TelegramContactHeader chat={chat({ display_name: null })} locale="de" />,
    );
    expect(screen.getByTestId("telegram-contact-name")).toHaveTextContent("@jona_m");
    rerender(
      <I18nextProvider i18n={i18n}>
        <TelegramContactHeader chat={chat({ display_name: null, username: null })} locale="de" />
      </I18nextProvider>,
    );
    expect(screen.getByTestId("telegram-contact-name")).toHaveTextContent("5550001");
  });
});
