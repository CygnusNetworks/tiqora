import { useState } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { RefineControls } from "./RefineControls";

const { refine, refineAvailability } = vi.hoisted(() => ({
  refine: vi.fn(),
  refineAvailability: vi.fn(),
}));

vi.mock("@/lib/refineApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/refineApi")>("@/lib/refineApi");
  return { ...actual, refineApi: { refine, refineAvailability } };
});

const QUOTE =
  "On 2026-09-12 08:30, kunde@example.org wrote:\n> internet geht nicht";

let qc: QueryClient;

beforeEach(() => {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  refine.mockReset();
  refineAvailability.mockReset();
  refineAvailability.mockResolvedValue({ available: true });
});

/** Renders the controls over a body the test can read back, exactly as the
 * composers wire them up. */
function Harness({ initial }: { initial: string }) {
  const [body, setBody] = useState(initial);
  return (
    <>
      <textarea
        data-testid="body"
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <RefineControls
        target={{ ticket_id: 42 }}
        body={body}
        onChange={setBody}
      />
    </>
  );
}

function renderHarness(initial: string) {
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <Harness initial={initial} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

function body(): string {
  return (screen.getByTestId("body") as HTMLTextAreaElement).value;
}

describe("RefineControls", () => {
  it("renders nothing while the queue has the feature disabled", async () => {
    refineAvailability.mockResolvedValue({ available: false });
    renderHarness("hallo");
    await waitFor(() => expect(refineAvailability).toHaveBeenCalled());
    expect(screen.queryByTestId("refine-button")).toBeNull();
  });

  it("sends only the segmented body and replaces the agent's text, keeping the quote", async () => {
    refine.mockResolvedValue({
      sections: [{ id: 0, text: "Guten Tag, das ist erledigt." }],
    });
    renderHarness(`erledigt\n\n${QUOTE}`);

    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeEnabled(),
    );
    fireEvent.click(screen.getByTestId("refine-button"));

    await waitFor(() =>
      expect(body()).toBe(`Guten Tag, das ist erledigt.\n\n${QUOTE}`),
    );
    const sent = refine.mock.calls[0][0];
    expect(sent.ticket_id).toBe(42);
    expect(sent.queue_id).toBeUndefined();
    expect(sent.segments.map((s: { kind: string }) => s.kind)).toEqual([
      "own",
      "quote",
    ]);
  });

  it("refines several own sections of an inline reply in one request", async () => {
    refine.mockResolvedValue({
      sections: [
        { id: 1, text: "Ja, das ist korrekt." },
        { id: 3, text: "Der Router wurde ebenfalls geprueft." },
      ],
    });
    renderHarness("> frage eins\nja stimmt\n> frage zwei\nrouter auch");

    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeEnabled(),
    );
    fireEvent.click(screen.getByTestId("refine-button"));

    await waitFor(() =>
      expect(body()).toBe(
        "> frage eins\nJa, das ist korrekt.\n> frage zwei\nDer Router wurde ebenfalls geprueft.",
      ),
    );
    expect(refine).toHaveBeenCalledTimes(1);
  });

  it("restores the text the agent wrote when undo is pressed", async () => {
    refine.mockResolvedValue({ sections: [{ id: 0, text: "Poliert." }] });
    renderHarness("roh getippt");

    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeEnabled(),
    );
    fireEvent.click(screen.getByTestId("refine-button"));
    await waitFor(() => expect(body()).toBe("Poliert."));

    fireEvent.click(screen.getByTestId("refine-undo"));
    expect(body()).toBe("roh getippt");
    expect(screen.queryByTestId("refine-undo")).toBeNull();
  });

  it("passes the selected tone through", async () => {
    refine.mockResolvedValue({ sections: [{ id: 0, text: "Kurz." }] });
    renderHarness("ein ziemlich langer text");

    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeEnabled(),
    );
    fireEvent.click(screen.getByTestId("refine-tone-select"));
    fireEvent.click(
      await screen.findByTestId("refine-tone-select-menu-option-concise"),
    );
    fireEvent.click(screen.getByTestId("refine-button"));

    await waitFor(() => expect(refine).toHaveBeenCalled());
    expect(refine.mock.calls[0][0].tone).toBe("concise");
  });

  it("disables the button when the composer holds only a quote", async () => {
    renderHarness(`\n\n${QUOTE}`);
    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeDisabled(),
    );
  });

  it("keeps the agent's text and explains itself when the model returns nothing usable", async () => {
    refine.mockRejectedValue(
      new ApiError(
        502,
        "refine_empty_output: no usable rewrite",
        "/api/v1/ai/refine",
      ),
    );
    renderHarness("roh getippt");

    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeEnabled(),
    );
    fireEvent.click(screen.getByTestId("refine-button"));

    await waitFor(() =>
      expect(screen.getByTestId("refine-error")).toBeInTheDocument(),
    );
    expect(body()).toBe("roh getippt");
    expect(screen.getByTestId("refine-error").textContent).toBe(
      i18n.t("ticket.refine.errorEmpty"),
    );
  });

  it("shows the rate-limit hint on 429", async () => {
    refine.mockRejectedValue(
      new ApiError(429, "limit reached", "/api/v1/ai/refine"),
    );
    renderHarness("roh getippt");

    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeEnabled(),
    );
    fireEvent.click(screen.getByTestId("refine-button"));

    await waitFor(() =>
      expect(screen.getByTestId("refine-error").textContent).toBe(
        i18n.t("ticket.refine.errorLimit"),
      ),
    );
  });
});
