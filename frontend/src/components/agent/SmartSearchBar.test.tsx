import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SmartSearchBar } from "./SmartSearchBar";
import type { SmartSearchValues } from "./smartSearch";

const customerQuickSearch = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { customerQuickSearch: (...a: unknown[]) => customerQuickSearch(...a) },
}));

const QUEUES = [
  { id: 7, name: "stoerungen" },
  { id: 9, name: "support" },
];
const AGENTS = [{ id: 3, full_name: "Tobias Beispiel", login: "t.beispiel" }];

const EMPTY: SmartSearchValues = { q: "", queueIds: [], stateTypes: [] };

function renderBar(values: SmartSearchValues, onPatch = vi.fn(), onSubmit = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SmartSearchBar
          values={values}
          queues={QUEUES}
          agents={AGENTS}
          onPatch={onPatch}
          onSubmitQuery={onSubmit}
        />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { onPatch, onSubmit };
}

describe("SmartSearchBar", () => {
  beforeEach(async () => {
    customerQuickSearch.mockReset().mockResolvedValue({ companies: [], contacts: [] });
    await i18n.changeLanguage("de");
  });

  it("recognises a queue: token and patches queue_id when picked", () => {
    const { onPatch } = renderBar(EMPTY);
    const input = screen.getByTestId("search-input");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "queue:sto" } });
    const opt = screen.getByTestId("smart-suggest-queue-7");
    expect(opt).toHaveTextContent("stoerungen");
    fireEvent.mouseDown(opt);
    expect(onPatch).toHaveBeenCalledWith({ queue_id: [7] });
  });

  it("recognises a status: token by German label", () => {
    const { onPatch } = renderBar(EMPTY);
    const input = screen.getByTestId("search-input");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "status:offen" } });
    fireEvent.mouseDown(screen.getByTestId("smart-suggest-status-open"));
    expect(onPatch).toHaveBeenCalledWith({ state_type: ["open"] });
  });

  it("parses a von: date in DD.MM.YYYY form", () => {
    const { onPatch } = renderBar(EMPTY);
    const input = screen.getByTestId("search-input");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "von:01.02.2026" } });
    fireEvent.mouseDown(screen.getByTestId("smart-suggest-date-from"));
    expect(onPatch).toHaveBeenCalledWith({ created_from: "2026-02-01" });
  });

  it("submits plain text as a full-text query on Enter", () => {
    const { onSubmit } = renderBar(EMPTY);
    const input = screen.getByTestId("search-input");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "wolfram" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith("wolfram");
  });

  it("renders a colored chip for an active queue filter and removes it", () => {
    const { onPatch } = renderBar({ ...EMPTY, queueIds: [7] });
    const chip = screen.getByTestId("smart-chip-q7");
    expect(chip).toHaveTextContent("stoerungen");
    fireEvent.mouseDown(screen.getByTestId("smart-chip-remove-q7"));
    expect(onPatch).toHaveBeenCalledWith({ queue_id: [] });
  });

  it("shows customer number on contact suggestions and in the chip label", async () => {
    customerQuickSearch.mockResolvedValue({
      companies: [],
      contacts: [
        {
          login: "mlas",
          first_name: "Marcus",
          last_name: "Lindqvist",
          email: "marcus@example.com",
          customer_id: "z90001",
          company_name: null,
        },
      ],
    });
    const { onPatch } = renderBar(EMPTY);
    const input = screen.getByTestId("search-input");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "kunde:z261" } });
    const opt = await screen.findByTestId("smart-suggest-ct-mlas");
    expect(opt).toHaveTextContent("Marcus Lindqvist");
    expect(opt).toHaveTextContent("z90001");
    fireEvent.mouseDown(opt);
    expect(onPatch).toHaveBeenCalledWith({
      customer_id: "z90001",
      customer_label: "Marcus Lindqvist · z90001",
    });
  });

  it("renders customer chip with name and customer number", () => {
    renderBar({
      ...EMPTY,
      customerId: "z90001",
      customerLabel: "Marcus Lindqvist · z90001",
    });
    expect(screen.getByTestId("smart-chip-customer")).toHaveTextContent("Marcus Lindqvist · z90001");
  });

  it("clears the input after picking a queue (no residual 'queue' free text)", () => {
    const onQueryChange = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <I18nextProvider i18n={i18n}>
          <SmartSearchBar
            values={EMPTY}
            queues={QUEUES}
            agents={AGENTS}
            onPatch={vi.fn()}
            onSubmitQuery={vi.fn()}
            onQueryChange={onQueryChange}
          />
        </I18nextProvider>
      </QueryClientProvider>,
    );
    const input = screen.getByTestId("search-input");
    fireEvent.focus(input);
    // Partial key must not become free-text query.
    fireEvent.change(input, { target: { value: "queue" } });
    expect(onQueryChange).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "queue:sto" } });
    fireEvent.mouseDown(screen.getByTestId("smart-suggest-queue-7"));
    expect(input).toHaveValue("");
  });

  it("auto-commits a unique queue match on trailing space", () => {
    const { onPatch } = renderBar(EMPTY);
    const input = screen.getByTestId("search-input");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "queue:stoerungen " } });
    expect(onPatch).toHaveBeenCalledWith({ queue_id: [7] });
    expect(input).toHaveValue("");
  });

  it("does not submit raw queue:… as fulltext when there is no match", () => {
    const { onSubmit } = renderBar(EMPTY);
    const input = screen.getByTestId("search-input");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "queue:does-not-exist" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("shows queue suggestions while typing queue:frag", () => {
    renderBar(EMPTY);
    const input = screen.getByTestId("search-input");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "queue:sto" } });
    expect(screen.getByTestId("smart-search-suggest")).toBeInTheDocument();
    expect(screen.getByTestId("smart-suggest-queue-7")).toHaveTextContent("stoerungen");
  });

  it("badges a typed ticket number and explains it below the field", () => {
    renderBar(EMPTY);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "2026010110000042" } });
    expect(screen.getByTestId("search-kind-ticket")).toHaveTextContent("Ticketnummer");
    expect(screen.getByTestId("search-readout")).toBeInTheDocument();
  });

  it("badges an e-mail address but leaves plain text unbadged", () => {
    renderBar(EMPTY);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "m.muster@uni.example.org" } });
    expect(screen.getByTestId("search-kind-email")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "Router gesperrt" } });
    expect(screen.queryByTestId("search-kind-email")).not.toBeInTheDocument();
    expect(screen.queryByTestId("search-readout")).not.toBeInTheDocument();
  });

  it("offers a typed date as a created-on filter without also searching for it", () => {
    const onPatch = vi.fn();
    const { onSubmit } = renderBar(EMPTY, onPatch);
    const input = screen.getByTestId("search-input");
    fireEvent.change(input, { target: { value: "22.09.2026" } });
    expect(screen.getByTestId("search-kind-date")).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByTestId("search-offer-date"));
    expect(onPatch).toHaveBeenCalledWith({
      created_from: "2026-09-22",
      created_to: "2026-09-22",
    });
    // The date turned into chips, so the field must not keep searching for it.
    expect(input).toHaveValue("");
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
