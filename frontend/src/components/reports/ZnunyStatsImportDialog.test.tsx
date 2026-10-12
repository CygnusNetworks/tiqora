import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { reportsApi, type StatPreview, type ZnunyStatsApplyOut, type ZnunyStatsPreview } from "@/lib/reportsApi";
import { ZnunyStatsImportDialog } from "./ZnunyStatsImportDialog";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, params, className }: { children: ReactNode; to: string; params?: Record<string, string>; className?: string }) => (
    <a href={to.replace(/\$(\w+)/g, (_, k: string) => params?.[k] ?? "")} className={className}>
      {children}
    </a>
  ),
}));

function Providers({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
    </QueryClientProvider>
  );
}

function stat(id: number, over: Partial<StatPreview>): StatPreview {
  return {
    stat_id: id,
    stat_number: String(10000 + id),
    import_key: `znuny.stat.${id}`,
    title: `rep52 Statistik ${id}`,
    description: "",
    object_label: "Matrix",
    target: "report",
    definition: null,
    already_imported: null,
    existing_id: null,
    issues: [],
    ...over,
  };
}

const PREVIEW: ZnunyStatsPreview = {
  stats: [
    stat(1, {
      title: "rep52 Tickets pro Monat",
      issues: [
        { element: "DynamicField_X", value: "1", reason: "Dynamische Felder stehen in Berichten noch nicht zur Verfügung.", severity: "dropped" },
        { element: "OrderBy", value: "Fixed=0", reason: "Zur Laufzeit änderbar.", severity: "approximated" },
      ],
    }),
    stat(2, { title: "rep52 Offene Tickets", target: "saved_search", object_label: "Ticketliste", already_imported: "saved_search", existing_id: 9 }),
    stat(3, { title: "rep52 Spezial", target: "none", object_label: "Dynamisches Objekt" }),
  ],
  errors: [{ stat_id: 8, message: "Nicht lesbar." }],
  truncated: false,
};
const APPLIED: ZnunyStatsApplyOut = {
  imported: [{ import_key: "znuny.stat.1", title: "rep52 Tickets pro Monat", target: "report", id: 41, action: "created", issues: [] }],
  skipped: [],
};

const renderDialog = (canCreate = true) =>
  render(
    <Providers>
      <ZnunyStatsImportDialog open onClose={() => {}} canCreate={canCreate} />
    </Providers>,
  );

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
afterEach(() => vi.restoreAllMocks());

describe("ZnunyStatsImportDialog", () => {
  it("lists stats with target badges and issues, applies the selection", async () => {
    vi.spyOn(reportsApi, "znunyStatsPreview").mockResolvedValue(PREVIEW);
    const apply = vi.spyOn(reportsApi, "znunyStatsApply").mockResolvedValue(APPLIED);
    renderDialog();
    const row1 = await screen.findByTestId("znuny-import-row-znuny.stat.1");
    expect(within(row1).getByText("Bericht")).toBeInTheDocument();
    expect(screen.getByText("Gespeicherte Suche")).toBeInTheDocument();
    expect(screen.getByText("Bereits übernommen – wird aktualisiert")).toBeInTheDocument();
    expect(within(screen.getByTestId("znuny-import-row-znuny.stat.3")).getByRole("checkbox")).toBeDisabled();
    expect(screen.getByTestId("znuny-import-apply")).toBeDisabled();

    fireEvent.click(within(row1).getByRole("button", { name: /Details/ }));
    expect(screen.getByText(/Dynamische Felder stehen in Berichten noch nicht zur Verfügung/)).toBeInTheDocument();
    expect(screen.getByText("Angepasst")).toBeInTheDocument();
    expect(screen.getByTestId("znuny-import-errors")).toHaveTextContent("#8: Nicht lesbar.");

    fireEvent.click(within(row1).getByRole("checkbox"));
    expect(screen.getByTestId("znuny-import-apply")).toHaveTextContent("Übernehmen (1)");
    fireEvent.click(screen.getByTestId("znuny-import-apply"));
    await waitFor(() => expect(apply).toHaveBeenCalledWith({ stat_ids: [1] }));
    expect(await screen.findByRole("link", { name: /rep52 Tickets pro Monat/ })).toHaveAttribute("href", "/agent/reports/41");
  });

  it("without create right apply stays disabled and says why", async () => {
    vi.spyOn(reportsApi, "znunyStatsPreview").mockResolvedValue(PREVIEW);
    renderDialog(false);
    const row1 = await screen.findByTestId("znuny-import-row-znuny.stat.1");
    fireEvent.click(within(row1).getByRole("checkbox"));
    expect(screen.getByTestId("znuny-import-apply")).toBeDisabled();
    expect(screen.getByText(/Berichte anzulegen/)).toBeInTheDocument();
  });

  it("file tab previews an uploaded file, applies it whole and shows load errors", async () => {
    vi.spyOn(reportsApi, "znunyStatsPreview").mockResolvedValue({ stats: [], errors: [], truncated: false });
    const filePreview = vi
      .spyOn(reportsApi, "znunyStatsFilePreview")
      .mockResolvedValue({ stats: [PREVIEW.stats[0]!], errors: [{ stat_id: null, message: "Eine Statistik ist nicht lesbar." }], truncated: false });
    const apply = vi.spyOn(reportsApi, "znunyStatsApply").mockResolvedValue(APPLIED);
    renderDialog();
    fireEvent.click(await screen.findByRole("tab", { name: "Aus einer Exportdatei" }));
    const file = new File(["<otrs_config/>"], "stat.xml", { type: "text/xml" });
    fireEvent.change(screen.getByTestId("znuny-import-file"), { target: { files: [file] } });
    await waitFor(() => expect(filePreview).toHaveBeenCalledWith(file));
    expect(await screen.findByTestId("znuny-import-row-znuny.stat.1")).toBeInTheDocument();
    expect(screen.getByTestId("znuny-import-errors")).toHaveTextContent("Eine Statistik ist nicht lesbar.");
    fireEvent.click(screen.getByTestId("znuny-import-apply"));
    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    expect(apply.mock.calls[0]![0]).toEqual({ file_content_base64: btoa("<otrs_config/>") });
  });

  it("shows the server message when the file preview fails", async () => {
    vi.spyOn(reportsApi, "znunyStatsPreview").mockResolvedValue({ stats: [], errors: [], truncated: false });
    vi.spyOn(reportsApi, "znunyStatsFilePreview").mockRejectedValue(
      new ApiError(422, { detail: { message: "Die Datei ist keine Znuny-Statistik." } }, "/api/v1/reports/import/znuny/file"),
    );
    renderDialog();
    fireEvent.click(await screen.findByRole("tab", { name: "Aus einer Exportdatei" }));
    fireEvent.change(screen.getByTestId("znuny-import-file"), { target: { files: [new File(["x"], "a.xml")] } });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("shows the empty state", async () => {
    vi.spyOn(reportsApi, "znunyStatsPreview").mockResolvedValue({ stats: [], errors: [], truncated: false });
    renderDialog();
    expect(await screen.findByTestId("znuny-import-empty")).toHaveTextContent(
      "In Znuny sind keine Statistiken gespeichert, die Sie sehen dürfen.",
    );
  });
});
