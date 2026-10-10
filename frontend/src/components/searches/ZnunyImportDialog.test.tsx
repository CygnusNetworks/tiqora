import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { demoZnunyPreview } from "@/demo/savedMock";
import { savedSearchesApi, type ZnunyImportPreview } from "@/lib/savedSearchesApi";
import { ZnunyImportDialog } from "./ZnunyImportDialog";
import { user } from "./searchTestUtils";

type P = ZnunyImportPreview["profiles"][number];
const cond = (field: string, value: unknown) => ({ field, op: "eq" as const, value });
const FULLTEXT = "Volltextsuche wird nicht unterstützt.";

function p(over: Partial<P> & { name: string }): P {
  return {
    criteria: { version: 1, where: { all: [cond("queue_id", 1), cond("priority_id", 3)] } },
    include_archived: false,
    existing_id: null,
    importable: true,
    partial: (over.unmapped ?? []).length > 0,
    unmapped: [],
    ...over,
  } as P;
}

const PREVIEW_THREE: ZnunyImportPreview = {
  profiles: [
    p({ name: "Offen Support" }),
    p({
      name: "Gemischt",
      unmapped: [{ key: "Subject", value: "y", reason: FULLTEXT }],
      existing_id: 4,
    }),
    p({
      name: "Volltext",
      importable: false,
      criteria: { version: 1 },
      unmapped: [{ key: "Fulltext", value: "drucker", reason: FULLTEXT }],
    }),
  ],
};

const mockPreview = (preview: ZnunyImportPreview) =>
  vi.spyOn(savedSearchesApi, "znunyPreview").mockResolvedValue(preview);

let qc: QueryClient;
function renderDialog(onClose = vi.fn()) {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ZnunyImportDialog open onClose={onClose} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return onClose;
}

beforeAll(async () => {
  window.scrollTo = vi.fn();
  await i18n.changeLanguage("de");
});
beforeEach(() => vi.restoreAllMocks());

describe("ZnunyImportDialog", () => {
  it("preselects complete profiles, flags partial and blocked ones, lists what is not taken over", async () => {
    mockPreview(PREVIEW_THREE);
    renderDialog();
    expect(await screen.findByTestId("znuny-import-row-Offen Support-check")).toBeChecked();
    expect(screen.getByTestId("znuny-import-row-Offen Support-summary")).toHaveTextContent("2 Bedingungen");
    // R-PARTIAL: importable but partly mapped -> badge, not preselected.
    expect(screen.getByTestId("znuny-import-row-Gemischt-check")).not.toBeChecked();
    expect(screen.getByTestId("znuny-import-row-Gemischt-check")).toBeEnabled();
    expect(screen.getByTestId("znuny-import-row-Gemischt-partial")).toHaveTextContent("nur teilweise übernommen");
    expect(screen.queryByTestId("znuny-import-row-Offen Support-partial")).toBeNull();
    expect(screen.getByTestId("znuny-import-row-Gemischt-update")).toHaveTextContent("wird aktualisiert");
    expect(screen.queryByTestId("znuny-import-row-Offen Support-update")).toBeNull();
    const blocked = screen.getByTestId("znuny-import-row-Volltext-check");
    expect(blocked).toBeDisabled();
    expect(blocked).not.toBeChecked();
    expect(screen.getByTestId("znuny-import-row-Volltext-summary")).toHaveTextContent("Keine Bedingung übernehmbar");

    expect(screen.queryByTestId("znuny-import-row-Gemischt-unmapped")).toBeNull();
    await user.click(screen.getByTestId("znuny-import-row-Gemischt-unmapped-toggle"));
    const list = screen.getByTestId("znuny-import-row-Gemischt-unmapped");
    expect(list).toHaveTextContent("Subject");
    expect(list).toHaveTextContent("y");
    expect(list).toHaveTextContent(FULLTEXT);
    expect(screen.getByTestId("znuny-import-apply")).toHaveTextContent("Übernehmen (1)");
    // The blocked profile's reason is reachable as well.
    await user.click(screen.getByTestId("znuny-import-row-Volltext-unmapped-toggle"));
    expect(screen.getByTestId("znuny-import-row-Volltext-unmapped")).toHaveTextContent("Fulltext");
  });

  it("labels each checkbox with the profile name", async () => {
    mockPreview(PREVIEW_THREE);
    renderDialog();
    expect(await screen.findByRole("checkbox", { name: "Offen Support" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Gemischt" })).not.toBeChecked();
  });

  it("applies the selection and shows the result", async () => {
    mockPreview(PREVIEW_THREE);
    const apply = vi.spyOn(savedSearchesApi, "znunyImport").mockResolvedValue({ created: [7], updated: [], skipped: [] });
    renderDialog();
    await user.click(await screen.findByTestId("znuny-import-apply"));
    expect(apply).toHaveBeenCalledWith(["Offen Support"]);
    expect(await screen.findByTestId("znuny-import-result")).toHaveTextContent("1 angelegt");
    expect(screen.queryByTestId("znuny-import-apply")).toBeNull();
  });

  it("can opt in to a partial profile and untick a complete one; result lists skipped with reasons", async () => {
    mockPreview(PREVIEW_THREE);
    const apply = vi.spyOn(savedSearchesApi, "znunyImport").mockResolvedValue({
      created: [],
      updated: [4],
      skipped: [{ name: "Offen Support", reason: "Name zu lang." }],
    });
    const invalidate = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    renderDialog();
    await user.click(await screen.findByTestId("znuny-import-row-Gemischt-check"));
    expect(screen.getByTestId("znuny-import-apply")).toHaveTextContent("Übernehmen (2)");
    await user.click(screen.getByTestId("znuny-import-apply"));
    expect(apply).toHaveBeenCalledWith(["Offen Support", "Gemischt"]);
    const result = await screen.findByTestId("znuny-import-result");
    expect(result).toHaveTextContent("1 aktualisiert");
    expect(result).toHaveTextContent("1 übersprungen");
    expect(screen.getByTestId("znuny-import-skipped")).toHaveTextContent("Name zu lang.");
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["saved-searches"] }));
  });

  it("disables the apply button when nothing is selected", async () => {
    mockPreview(PREVIEW_THREE);
    const apply = vi.spyOn(savedSearchesApi, "znunyImport");
    renderDialog();
    await user.click(await screen.findByTestId("znuny-import-row-Offen Support-check"));
    const btn = screen.getByTestId("znuny-import-apply");
    expect(btn).toBeDisabled();
    await user.click(btn);
    expect(apply).not.toHaveBeenCalled();
  });

  it("says so when Znuny has no profiles", async () => {
    mockPreview({ profiles: [] });
    renderDialog();
    expect(await screen.findByTestId("znuny-import-empty")).toHaveTextContent("keine Suchvorlagen");
    expect(screen.queryByTestId("znuny-import-apply")).toBeNull();
  });

  it("shows an error when the preview cannot be loaded", async () => {
    vi.spyOn(savedSearchesApi, "znunyPreview").mockRejectedValue(new Error("Znuny nicht erreichbar"));
    renderDialog();
    expect(await screen.findByTestId("znuny-import-error")).toHaveTextContent("Znuny nicht erreichbar");
  });

  it("works with the demo-mode preview", async () => {
    mockPreview(demoZnunyPreview());
    renderDialog();
    expect(await screen.findByTestId("znuny-import-row-Offen in Support-check")).toBeChecked();
    expect(screen.getByTestId("znuny-import-row-Gemischt-check")).not.toBeChecked();
    await user.click(screen.getByTestId("znuny-import-row-Gemischt-unmapped-toggle"));
    expect(screen.getByTestId("znuny-import-row-Gemischt-unmapped")).toHaveTextContent("Fulltext");
  });

  it("counts nested conditions in the summary and uses the singular for one", async () => {
    mockPreview({
      profiles: [
        p({
          name: "Verschachtelt",
          criteria: {
            version: 1,
            where: { all: [cond("a", 1), { any: [cond("b", 2), { not: cond("c", 3) }] }] },
          },
        }),
        p({ name: "Eine", criteria: { version: 1, where: cond("a", 1) } }),
      ],
    });
    renderDialog();
    expect(await screen.findByTestId("znuny-import-row-Verschachtelt-summary")).toHaveTextContent("3 Bedingungen");
    expect(screen.getByTestId("znuny-import-row-Eine-summary")).toHaveTextContent("1 Bedingung wird");
  });
});
