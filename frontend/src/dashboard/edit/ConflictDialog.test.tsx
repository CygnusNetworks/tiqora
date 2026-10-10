import { beforeAll, expect, test, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import i18n from "@/i18n";
import { ConflictDialog } from "./ConflictDialog";

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

test("an alert dialog with 'Neu laden' and 'Weiter bearbeiten'; Escape keeps editing", () => {
  const onReload = vi.fn();
  const onContinue = vi.fn();
  const { rerender } = render(<ConflictDialog open={false} onReload={onReload} onContinue={onContinue} />);
  expect(screen.queryByRole("alertdialog")).toBeNull();
  rerender(<ConflictDialog open onReload={onReload} onContinue={onContinue} />);
  const dialog = screen.getByRole("alertdialog", { name: "Dieses Dashboard wurde inzwischen geändert" });
  expect(dialog).toHaveTextContent("Deine Änderungen bleiben als Entwurf erhalten");
  expect(dialog).toHaveTextContent("ersetzen sie die neue Fassung dann vollständig; sie werden nicht zusammengeführt");
  fireEvent.click(within(dialog).getByRole("button", { name: "Neu laden" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Weiter bearbeiten" }));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onReload).toHaveBeenCalledTimes(1);
  expect(onContinue).toHaveBeenCalledTimes(2);
});

test("for a role template the text says that restoring replaces other people's changes for all members", () => {
  render(<ConflictDialog open template onReload={vi.fn()} onContinue={vi.fn()} />);
  expect(screen.getByRole("alertdialog")).toHaveTextContent(
    "auch die Änderungen anderer, für alle, die die Vorlage über ihre Rolle bekommen",
  );
});
