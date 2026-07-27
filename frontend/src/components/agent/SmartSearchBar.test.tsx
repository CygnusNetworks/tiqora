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
const AGENTS = [{ id: 3, full_name: "Thomas Valerius", login: "t.valerius" }];

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
});
