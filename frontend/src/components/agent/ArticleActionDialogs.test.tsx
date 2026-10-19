import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { BounceDialog, ForwardDialog } from "./ArticleActionDialogs";

const { forwardArticle, bounceArticle } = vi.hoisted(() => ({
  forwardArticle: vi.fn(),
  bounceArticle: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      forwardArticle,
      bounceArticle,
      acquireTicketLock: vi.fn().mockResolvedValue({ result: "acquired" }),
      getCryptoOptions: vi.fn().mockResolvedValue({ enabled: false }),
    },
  };
});

function renderDialog(
  dialog = <ForwardDialog ticketId={7} articleId={3} open onClose={() => {}} />,
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{dialog}</I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("ForwardDialog", () => {
  beforeEach(() => {
    forwardArticle.mockReset().mockResolvedValue({ article_id: 99 });
    void i18n.changeLanguage("en");
  });

  it("sends (not saves) and says the original goes along", async () => {
    renderDialog();
    expect(screen.getByTestId("forward-includes-original")).toHaveTextContent(
      "The original message and its attachments are sent below your note.",
    );
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    const [to, note] = screen.getAllByRole("textbox");
    fireEvent.change(to, { target: { value: "colleague@example.com" } });
    fireEvent.change(note, { target: { value: "FYI" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    // The server builds the Fwd: subject, the quoted original and the attachments.
    await waitFor(() =>
      expect(forwardArticle).toHaveBeenCalledWith(7, 3, {
        to_address: "colleague@example.com",
        note: "FYI",
        body: "",
        email_security: null,
      }),
    );
  });
});

describe("BounceDialog", () => {
  beforeEach(() => {
    bounceArticle.mockReset();
    void i18n.changeLanguage("en");
  });

  it("sends and shows why a bounce was refused", async () => {
    bounceArticle.mockRejectedValue(
      new ApiError(422, "Outbound mail is disabled; an article cannot be bounced", "/bounce"),
    );
    renderDialog(<BounceDialog ticketId={7} articleId={3} open onClose={() => {}} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "other@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(screen.getByTestId("bounce-error")).toHaveTextContent(
        "Outbound mail is disabled; an article cannot be bounced",
      ),
    );
    expect(bounceArticle).toHaveBeenCalledWith(7, 3, { to_address: "other@example.com" });
  });
});
