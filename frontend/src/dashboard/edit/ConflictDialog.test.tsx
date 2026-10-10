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
  expect(dialog).toHaveTextContent("deine Änderungen bleiben als Entwurf erhalten");
  fireEvent.click(within(dialog).getByRole("button", { name: "Neu laden" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Weiter bearbeiten" }));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onReload).toHaveBeenCalledTimes(1);
  expect(onContinue).toHaveBeenCalledTimes(2);
});
