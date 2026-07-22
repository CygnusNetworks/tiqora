import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { HelpPopover } from "./HelpPopover";

function renderPopover() {
  return render(
    <I18nextProvider i18n={i18n}>
      <HelpPopover title="Autonomie" defaultHint="Default: off" testId="help-autonomy">
        Steuert, ob die KI selbstständig antworten darf.
      </HelpPopover>
    </I18nextProvider>,
  );
}

describe("HelpPopover", () => {
  it("is closed by default and opens on click", () => {
    renderPopover();
    expect(screen.queryByTestId("help-autonomy-panel")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("help-autonomy"));
    expect(screen.getByTestId("help-autonomy-panel")).toBeInTheDocument();
    expect(screen.getByText("Autonomie")).toBeInTheDocument();
    expect(screen.getByText(/selbstständig antworten/)).toBeInTheDocument();
    expect(screen.getByTestId("help-autonomy-default")).toHaveTextContent("Default: off");
    expect(screen.getByTestId("help-autonomy")).toHaveAttribute("aria-expanded", "true");
  });

  it("closes on a second click of the trigger", () => {
    renderPopover();
    fireEvent.click(screen.getByTestId("help-autonomy"));
    expect(screen.getByTestId("help-autonomy-panel")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("help-autonomy"));
    expect(screen.queryByTestId("help-autonomy-panel")).not.toBeInTheDocument();
  });

  it("closes on Escape", () => {
    renderPopover();
    fireEvent.click(screen.getByTestId("help-autonomy"));
    expect(screen.getByTestId("help-autonomy-panel")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("help-autonomy-panel")).not.toBeInTheDocument();
  });

  it("closes on an outside click", () => {
    renderPopover();
    fireEvent.click(screen.getByTestId("help-autonomy"));
    expect(screen.getByTestId("help-autonomy-panel")).toBeInTheDocument();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByTestId("help-autonomy-panel")).not.toBeInTheDocument();
  });

  it("omits the default-hint footer when none is given", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <HelpPopover title="KB-Tags" testId="help-kb-tags">
          Kommagetrennte Tags zur Filterung der Wissensdatenbank.
        </HelpPopover>
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByTestId("help-kb-tags"));
    expect(screen.queryByTestId("help-kb-tags-default")).not.toBeInTheDocument();
  });
});
