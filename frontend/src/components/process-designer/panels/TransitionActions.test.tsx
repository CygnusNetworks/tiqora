import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { EntitySummary } from "@/lib/processDesignerApi";
import { TransitionActions } from "./TransitionActions";

const library: EntitySummary[] = [
  { entity_type: "TransitionAction", entity_id: "TA-9", name: "Fremde Aktion", used_by: ["Process-2"], sync_state: "synced" },
];

beforeEach(async () => {
  await i18n.changeLanguage("de");
});

describe("TransitionActions", () => {
  it("adding an existing action keeps edits made while it is being loaded", async () => {
    let finish!: () => void;
    const adoptAction = vi.fn(() => new Promise<void>((r) => (finish = r)));
    const onChange = vi.fn();
    const ui = (ids: string[], cb: (ids: string[]) => void) => (
      <I18nextProvider i18n={i18n}>
        <TransitionActions
          actionIds={ids}
          actions={[]}
          library={library}
          adoptAction={adoptAction}
          createAction={vi.fn()}
          onChange={cb}
          onSelect={vi.fn()}
        />
      </I18nextProvider>
    );
    const { rerender } = render(ui(["TA-1"], vi.fn()));
    fireEvent.click(screen.getByTestId("transition-actions-add"));
    fireEvent.click(await screen.findByText("Fremde Aktion"));
    // While the fetch runs, the list changes (TA-2 added elsewhere) and the handler is replaced.
    rerender(ui(["TA-1", "TA-2"], onChange));
    await act(async () => finish());
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(["TA-1", "TA-2", "TA-9"]));
  });
});
