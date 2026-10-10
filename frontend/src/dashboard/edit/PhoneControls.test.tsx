import { beforeAll, expect, test, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { PhoneControls } from "./PhoneControls";

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

test("up, down and settings call back; the arrows are not part of the names", () => {
  const onShift = vi.fn();
  const onSettings = vi.fn();
  render(<PhoneControls widgetId="w" isFirst={false} isLast={false} onShift={onShift} onSettings={onSettings} />);
  fireEvent.click(screen.getByRole("button", { name: "Nach oben" }));
  fireEvent.click(screen.getByRole("button", { name: "Nach unten" }));
  fireEvent.click(screen.getByRole("button", { name: "Einstellungen" }));
  expect(onShift.mock.calls).toEqual([["up"], ["down"]]);
  expect(onSettings).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Nach oben" })).toHaveClass("min-h-11");
});

test("the first widget cannot go up, the last cannot go down", () => {
  const { rerender } = render(<PhoneControls widgetId="w" isFirst isLast={false} onShift={vi.fn()} onSettings={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Nach oben" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Nach unten" })).toBeEnabled();
  rerender(<PhoneControls widgetId="w" isFirst={false} isLast onShift={vi.fn()} onSettings={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Nach unten" })).toBeDisabled();
});
