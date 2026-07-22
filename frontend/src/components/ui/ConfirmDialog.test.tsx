import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { useConfirm, usePrompt } from "./ConfirmDialog";

function ConfirmHarness({ onResult }: { onResult: (v: boolean) => void }) {
  const { confirm, dialog } = useConfirm();
  return (
    <div>
      <button
        data-testid="ask"
        onClick={async () => {
          const result = await confirm({ title: "Delete?", message: "Are you sure?" });
          onResult(result);
        }}
      >
        ask
      </button>
      {dialog}
    </div>
  );
}

function PromptHarness({ onResult }: { onResult: (v: string | null) => void }) {
  const { prompt, dialog } = usePrompt();
  return (
    <div>
      <button
        data-testid="ask"
        onClick={async () => {
          const result = await prompt({ title: "Name it", defaultValue: "Default" });
          onResult(result);
        }}
      >
        ask
      </button>
      {dialog}
    </div>
  );
}

function renderWithI18n(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("useConfirm", () => {
  it("renders no dialog until asked", () => {
    renderWithI18n(<ConfirmHarness onResult={vi.fn()} />);
    expect(screen.queryByTestId("confirm-dialog")).not.toBeInTheDocument();
  });

  it("resolves true when confirmed", async () => {
    const onResult = vi.fn();
    renderWithI18n(<ConfirmHarness onResult={onResult} />);

    fireEvent.click(screen.getByTestId("ask"));
    expect(await screen.findByTestId("confirm-dialog")).toBeInTheDocument();
    expect(screen.getByText("Are you sure?")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
    expect(screen.queryByTestId("confirm-dialog")).not.toBeInTheDocument();
  });

  it("resolves false when cancelled", async () => {
    const onResult = vi.fn();
    renderWithI18n(<ConfirmHarness onResult={onResult} />);

    fireEvent.click(screen.getByTestId("ask"));
    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });
});

describe("usePrompt", () => {
  it("resolves the (possibly edited) input value on confirm", async () => {
    const onResult = vi.fn();
    renderWithI18n(<PromptHarness onResult={onResult} />);

    fireEvent.click(screen.getByTestId("ask"));
    const input = await screen.findByTestId("confirm-dialog-input");
    expect(input).toHaveValue("Default");

    fireEvent.change(input, { target: { value: "My Passkey" } });
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith("My Passkey"));
  });

  it("resolves null on cancel", async () => {
    const onResult = vi.fn();
    renderWithI18n(<PromptHarness onResult={onResult} />);

    fireEvent.click(screen.getByTestId("ask"));
    await screen.findByTestId("confirm-dialog-input");
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(null));
  });
});
