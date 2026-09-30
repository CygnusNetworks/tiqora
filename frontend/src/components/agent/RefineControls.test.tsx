import { useState } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { RefineControls, type RefineResult } from "./RefineControls";
import { RefineDiffView } from "./RefineDiffView";
import { useRefineReview } from "./useRefineReview";

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
  // The tone is remembered in localStorage on purpose, so it survives
  // between tests unless each one starts from a clean slate.
  window.localStorage.removeItem("tiqora-refine-tone");
  refine.mockReset();
  refineAvailability.mockReset();
  refineAvailability.mockResolvedValue({ available: true });
});

/** Renders the controls over a body the test can read back, exactly as the
 * composers wire them up. */
function Harness({
  initial,
  variant,
  mode,
}: {
  initial: string;
  variant?: "split" | "toolbar";
  mode?: "message" | "call_note";
}) {
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
        variant={variant}
        mode={mode}
      />
    </>
  );
}

function renderHarness(
  initial: string,
  variant?: "split" | "toolbar",
  mode?: "message" | "call_note",
) {
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <Harness initial={initial} variant={variant} mode={mode} />
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
    fireEvent.click(screen.getByTestId("refine-tone-trigger"));
    fireEvent.click(await screen.findByTestId("refine-tone-concise"));
    fireEvent.click(screen.getByTestId("refine-button"));

    await waitFor(() => expect(refine).toHaveBeenCalled());
    expect(refine.mock.calls[0][0].tone).toBe("concise");
  });

  it("disables the button and says why when the composer holds only a quote", async () => {
    renderHarness(`\n\n${QUOTE}`);
    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeDisabled(),
    );
    expect(screen.getByTestId("refine-tone-chip").textContent).toBe(
      i18n.t("ticket.refine.nothingToRefineShort"),
    );
  });

  it("shows the active tone next to the button so the click is predictable", async () => {
    renderHarness("roh getippt");
    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeEnabled(),
    );
    expect(screen.getByTestId("refine-tone-chip").textContent).toBe(
      i18n.t("ticket.refine.toneStandard"),
    );
  });

  it("remembers the tone for the next composer", async () => {
    refine.mockResolvedValue({ sections: [{ id: 0, text: "Kurz." }] });
    const first = renderHarness("ein ziemlich langer text");
    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeEnabled(),
    );
    fireEvent.click(screen.getByTestId("refine-tone-trigger"));
    fireEvent.click(await screen.findByTestId("refine-tone-concise"));
    first.unmount();

    renderHarness("noch ein text");
    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeEnabled(),
    );
    expect(screen.getByTestId("refine-tone-chip").textContent).toBe(
      i18n.t("ticket.refine.toneConcise"),
    );
  });

  it("keeps the agent's text and explains itself when the model returns nothing usable", async () => {
    refine.mockRejectedValue(
      new ApiError(
        502,
        { detail: "refine_empty_output: no usable rewrite" },
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

  it("names the queue switch when the backend reports the feature disabled", async () => {
    refine.mockRejectedValue(
      new ApiError(
        409,
        { detail: "refine_disabled: Refine is disabled for queue 5" },
        "/api/v1/ai/refine",
      ),
    );
    renderHarness("roh getippt");

    await waitFor(() =>
      expect(screen.getByTestId("refine-button")).toBeEnabled(),
    );
    fireEvent.click(screen.getByTestId("refine-button"));

    await waitFor(() =>
      expect(screen.getByTestId("refine-error").textContent).toBe(
        i18n.t("ticket.refine.errorDisabled"),
      ),
    );
  });

  it("shows the rate-limit hint on 429", async () => {
    refine.mockRejectedValue(
      new ApiError(429, { detail: "limit reached" }, "/api/v1/ai/refine"),
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

describe("RefineControls toolbar variant", () => {
  it("shows the tones as a visible switch and refines with the picked one", async () => {
    refine.mockResolvedValue({ sections: [{ id: 0, text: "Sehr geehrte Frau Muster, erledigt." }] });
    renderHarness("erledigt", "toolbar");

    await waitFor(() => expect(screen.getByTestId("refine-button")).toBeEnabled());
    expect(screen.getByTestId("refine-toolbar")).toBeInTheDocument();
    // No dropdown in this variant — every tone is one click away.
    expect(screen.queryByTestId("refine-tone-trigger")).toBeNull();
    expect(screen.getByTestId("refine-tone-standard")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("refine-tone-formal"));
    expect(screen.getByTestId("refine-tone-formal")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByTestId("refine-button"));

    await waitFor(() => expect(body()).toBe("Sehr geehrte Frau Muster, erledigt."));
    expect(refine.mock.calls[0][0].tone).toBe("formal");
    expect(screen.getByTestId("refine-undo")).toBeInTheDocument();
  });

  it("keeps the explanation behind a help button instead of a line of text", async () => {
    renderHarness("erledigt", "toolbar");
    await waitFor(() => expect(screen.getByTestId("refine-button")).toBeEnabled());
    expect(screen.getByTestId("refine-help")).toBeInTheDocument();
    expect(screen.queryByText(/Quotes stay unchanged|Zitate bleiben unverändert/)).toBeNull();
  });
});

describe("RefineControls call-note mode", () => {
  it("offers one structure button without tones and sends mode + UI language", async () => {
    const note = "Anliegen\n- Drucker\nVereinbart\n- Techniker Di\nNächste Schritte\n-";
    refine.mockResolvedValue({ sections: [{ id: 0, text: note }] });
    renderHarness("drucker kaputt, techniker di", "toolbar", "call_note");

    await waitFor(() => expect(screen.getByTestId("refine-button")).toBeEnabled());
    expect(screen.queryByTestId("refine-tone-standard")).toBeNull();
    fireEvent.click(screen.getByTestId("refine-button"));

    await waitFor(() => expect(body()).toBe(note));
    const sent = refine.mock.calls[0][0];
    expect(sent.mode).toBe("call_note");
    expect(sent.language).toBe(i18n.language);
    expect(sent.ticket_id).toBe(42);
  });
});

/** Composer wiring with the review, exactly as the three composers do it. */
function ReviewHarness({
  initial,
  onRefinedSpy,
}: {
  initial: string;
  onRefinedSpy?: (r: RefineResult) => void;
}) {
  const [text, setText] = useState(initial);
  const rr = useRefineReview(setText);
  return (
    <>
      {rr.review ? (
        <RefineDiffView
          key={rr.reviewKey}
          before={rr.review.before}
          after={rr.review.after}
          toneLabel="t"
          onAccept={rr.accept}
          onDiscard={rr.discard}
        />
      ) : (
        <textarea
          data-testid="body"
          value={text}
          onChange={(e) => rr.onEdit(e.target.value)}
        />
      )}
      <RefineControls
        target={{ ticket_id: 42 }}
        body={text}
        onChange={rr.onEdit}
        onRefined={(r) => {
          onRefinedSpy?.(r);
          rr.onRefined(r);
        }}
        appliedStats={rr.applied?.stats ?? null}
        onShowChanges={rr.showChanges}
        reviewOpen={rr.review !== null}
      />
    </>
  );
}

function renderReview(initial: string, spy?: (r: RefineResult) => void) {
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ReviewHarness initial={initial} onRefinedSpy={spy} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

async function pressRefine() {
  await waitFor(() => expect(screen.getByTestId("refine-button")).toBeEnabled());
  fireEvent.click(screen.getByTestId("refine-button"));
}

describe("RefineControls with review (onRefined)", () => {
  it("hands the result to the parent instead of changing the body", async () => {
    refine.mockResolvedValue({ sections: [{ id: 0, text: "Poliert." }] });
    const onRefined = vi.fn();
    const onChange = vi.fn();
    render(
      <QueryClientProvider client={qc}>
        <I18nextProvider i18n={i18n}>
          <RefineControls
            target={{ ticket_id: 42 }}
            body="roh getippt"
            onChange={onChange}
            onRefined={onRefined}
          />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    await pressRefine();
    await waitFor(() => expect(onRefined).toHaveBeenCalledTimes(1));
    expect(onRefined).toHaveBeenCalledWith({
      before: "roh getippt",
      after: "Poliert.",
      tone: "standard",
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("opens no review and says so when nothing changed", async () => {
    refine.mockResolvedValue({ sections: [{ id: 0, text: "schon gut" }] });
    const spy = vi.fn();
    renderReview("schon gut", spy);
    await pressRefine();
    expect(
      await screen.findByText(i18n.t("ticket.refine.review.noChanges")),
    ).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
    expect(screen.queryByTestId("refine-review")).toBeNull();
    expect(body()).toBe("schon gut");
  });

  it("shows the applied summary, re-opens the changes and undoes to the pre-refine text", async () => {
    refine.mockResolvedValue({ sections: [{ id: 0, text: "Poliert." }] });
    renderReview("roh getippt");
    await pressRefine();
    await screen.findByTestId("refine-review");
    expect(screen.queryByTestId("refine-undo")).toBeNull();

    fireEvent.click(screen.getByTestId("refine-review-accept"));
    expect(body()).toBe("Poliert.");
    expect(screen.getByTestId("refine-applied").textContent).toContain(
      i18n.t("ticket.refine.review.applied", { tone: "Standard", on: 1, total: 1 }),
    );

    fireEvent.click(screen.getByTestId("refine-show-changes"));
    expect(screen.getByTestId("refine-review")).toBeTruthy();
    // No undo while the review is open.
    expect(screen.queryByTestId("refine-undo")).toBeNull();
    fireEvent.click(screen.getByTestId("refine-review-discard"));
    expect(body()).toBe("Poliert.");

    fireEvent.click(screen.getByTestId("refine-undo"));
    expect(body()).toBe("roh getippt");
  });

  it("drops the no-changes message once the text is edited", async () => {
    refine.mockResolvedValue({ sections: [{ id: 0, text: "schon gut" }] });
    renderReview("schon gut");
    await pressRefine();
    await screen.findByTestId("refine-no-changes");
    fireEvent.change(screen.getByTestId("body"), { target: { value: "schon gut!" } });
    expect(screen.queryByTestId("refine-no-changes")).toBeNull();
  });

  it("compares a second refine against the text right before it", async () => {
    refine.mockResolvedValueOnce({ sections: [{ id: 0, text: "Eins zwei." }] });
    const spy = vi.fn();
    renderReview("eins zwei", spy);
    await pressRefine();
    fireEvent.click(await screen.findByTestId("refine-review-accept"));
    expect(body()).toBe("Eins zwei.");

    refine.mockResolvedValueOnce({ sections: [{ id: 0, text: "Eins zwei drei." }] });
    await pressRefine();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy.mock.calls[1][0].before).toBe("Eins zwei.");
    fireEvent.click(await screen.findByTestId("refine-review-accept"));
    expect(body()).toBe("Eins zwei drei.");

    fireEvent.click(screen.getByTestId("refine-undo"));
    expect(body()).toBe("Eins zwei.");
  });
});
