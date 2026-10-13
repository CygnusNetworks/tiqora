import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SwitchDialog } from "./SwitchDialog";

const switchToTiqora = vi.fn();
vi.mock("@/lib/itsmImportApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/itsmImportApi")>()),
  itsmImportApi: { switchToTiqora: (...a: unknown[]) => switchToTiqora(...a) },
}));

const onClose = vi.fn();
const onStarted = vi.fn();

function renderDialog(withoutCiData = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SwitchDialog open onClose={onClose} onStarted={onStarted} withoutCiData={withoutCiData} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const type = (text: string) =>
  fireEvent.change(screen.getByTestId("itsm-switch-confirm"), { target: { value: text } });

describe("SwitchDialog", () => {
  beforeEach(async () => {
    switchToTiqora.mockReset().mockResolvedValue({ job_id: 42, status: "queued" });
    onClose.mockReset();
    onStarted.mockReset();
    await i18n.changeLanguage("de");
  });

  it("lists what happens, in order", () => {
    renderDialog();
    expect(screen.getByText(/Znuny-Bildschirme zum Bearbeiten von CIs werden gesperrt/)).toBeInTheDocument();
    expect(screen.getByText(/Tiqora übernimmt ein letztes Mal alle Änderungen aus Znuny/)).toBeInTheDocument();
    expect(screen.getByText(/pflegen Sie CIs nur noch in Tiqora/)).toBeInTheDocument();
  });

  it("keeps submit disabled until UMSTELLEN is typed", () => {
    renderDialog();
    fireEvent.click(screen.getByLabelText("Die Erweiterung TiqoraITSM ist in Znuny installiert"));
    expect(screen.getByTestId("itsm-switch-submit")).toBeDisabled();
    type("umstellen");
    expect(screen.getByTestId("itsm-switch-submit")).toBeDisabled();
    type("UMSTELLEN");
    expect(screen.getByTestId("itsm-switch-submit")).toBeEnabled();
  });

  it("needs a choice about the add-on even with the word typed", () => {
    renderDialog();
    type("UMSTELLEN");
    expect(screen.getByTestId("itsm-switch-submit")).toBeDisabled();
  });

  it("sends addon_installed with the add-on installed", async () => {
    renderDialog();
    fireEvent.click(screen.getByLabelText("Die Erweiterung TiqoraITSM ist in Znuny installiert"));
    expect(screen.queryByLabelText("In Znuny werden keine CIs mehr bearbeitet")).not.toBeInTheDocument();
    type("UMSTELLEN");
    fireEvent.click(screen.getByTestId("itsm-switch-submit"));
    await waitFor(() =>
      expect(switchToTiqora).toHaveBeenCalledWith({
        confirm: "UMSTELLEN",
        addon_installed: true,
        edits_stopped: false,
        without_ci_data: false,
      }),
    );
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith(42));
    expect(onClose).toHaveBeenCalled();
  });

  it("without the add-on the checkbox is required and edits_stopped is sent", async () => {
    renderDialog();
    fireEvent.click(screen.getByLabelText("Die Erweiterung TiqoraITSM ist in Znuny nicht installiert"));
    type("UMSTELLEN");
    expect(screen.getByTestId("itsm-switch-submit")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("In Znuny werden keine CIs mehr bearbeitet"));
    expect(screen.getByTestId("itsm-switch-submit")).toBeEnabled();
    fireEvent.click(screen.getByTestId("itsm-switch-submit"));
    await waitFor(() =>
      expect(switchToTiqora).toHaveBeenCalledWith({
        confirm: "UMSTELLEN",
        addon_installed: false,
        edits_stopped: true,
        without_ci_data: false,
      }),
    );
  });

  it("the path without CI data needs its own confirmation and sends without_ci_data", async () => {
    renderDialog(true);
    fireEvent.click(screen.getByLabelText("Die Erweiterung TiqoraITSM ist in Znuny installiert"));
    type("UMSTELLEN");
    expect(screen.getByTestId("itsm-switch-submit")).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/CI-Daten aus Znuny nicht übernommen werden/));
    fireEvent.click(screen.getByTestId("itsm-switch-submit"));
    await waitFor(() =>
      expect(switchToTiqora).toHaveBeenCalledWith({
        confirm: "UMSTELLEN",
        addon_installed: true,
        edits_stopped: false,
        without_ci_data: true,
      }),
    );
  });

  it("shows the API error and stays open", async () => {
    switchToTiqora.mockRejectedValue(new Error("boom"));
    renderDialog();
    fireEvent.click(screen.getByLabelText("Die Erweiterung TiqoraITSM ist in Znuny installiert"));
    type("UMSTELLEN");
    fireEvent.click(screen.getByTestId("itsm-switch-submit"));
    expect(await screen.findByTestId("itsm-switch-error")).toBeInTheDocument();
    expect(onStarted).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
