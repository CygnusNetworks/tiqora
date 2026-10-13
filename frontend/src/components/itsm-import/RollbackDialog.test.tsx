import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { RollbackDialog } from "./RollbackDialog";
import { itsmImportKeys, type ImportStatus } from "@/lib/itsmImportApi";

const rollback = vi.fn();
vi.mock("@/lib/itsmImportApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/itsmImportApi")>()),
  itsmImportApi: { rollback: (...a: unknown[]) => rollback(...a) },
}));

const onClose = vi.fn();
const MODE_A = { mode: "znuny_readthrough" } as ImportStatus;

function renderDialog() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RollbackDialog open onClose={onClose} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { qc, invalidate };
}

const type = (text: string) =>
  fireEvent.change(screen.getByTestId("itsm-rollback-confirm"), { target: { value: text } });

describe("RollbackDialog", () => {
  beforeEach(async () => {
    rollback.mockReset().mockResolvedValue(MODE_A);
    onClose.mockReset();
    await i18n.changeLanguage("de");
  });

  it("explains the consequence", () => {
    renderDialog();
    expect(
      screen.getByText(/Tiqora zeigt dann wieder die CIs aus Znuny\. Änderungen, die Sie seit der Umstellung in Tiqora gemacht haben, sind in Znuny nicht sichtbar\./),
    ).toBeInTheDocument();
  });

  it("keeps submit disabled until ZURÜCK is typed", () => {
    renderDialog();
    expect(screen.getByTestId("itsm-rollback-submit")).toBeDisabled();
    type("zurück");
    expect(screen.getByTestId("itsm-rollback-submit")).toBeDisabled();
    type("ZURÜCK");
    expect(screen.getByTestId("itsm-rollback-submit")).toBeEnabled();
  });

  it("accepts a decomposed Ü and the spelling ZURUECK, and sends ZURÜCK", async () => {
    renderDialog();
    type("ZURU\u0308CK");
    expect(screen.getByTestId("itsm-rollback-submit")).toBeEnabled();
    type(" ZURUECK ");
    expect(screen.getByTestId("itsm-rollback-submit")).toBeEnabled();
    fireEvent.click(screen.getByTestId("itsm-rollback-submit"));
    await waitFor(() => expect(rollback).toHaveBeenCalledWith({ confirm: "ZURÜCK" }));
  });

  it("sends the confirmation and refreshes the status and the runs", async () => {
    const { qc, invalidate } = renderDialog();
    type("ZURÜCK");
    fireEvent.click(screen.getByTestId("itsm-rollback-submit"));
    await waitFor(() => expect(rollback).toHaveBeenCalledWith({ confirm: "ZURÜCK" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(invalidate).toHaveBeenCalledWith({ queryKey: itsmImportKeys.status() });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: itsmImportKeys.runs() });
    expect(qc.getQueryData(itsmImportKeys.status())).toEqual(MODE_A);
  });

  it("shows the API error and stays open", async () => {
    rollback.mockRejectedValue(new Error("boom"));
    renderDialog();
    type("ZURÜCK");
    fireEvent.click(screen.getByTestId("itsm-rollback-submit"));
    expect(await screen.findByTestId("itsm-rollback-error")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
