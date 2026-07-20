import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { MonthGrid } from "./MonthGrid";
import type { OccurrenceOut } from "@/lib/api";

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

const occurrences: OccurrenceOut[] = [
  {
    appointment_id: 1,
    calendar_id: 10,
    title: "Kickoff",
    description: null,
    location: null,
    start_time: "2026-03-05T09:00:00",
    end_time: "2026-03-05T10:00:00",
    all_day: false,
    is_recurring: false,
  },
  {
    appointment_id: 2,
    calendar_id: 10,
    title: "Standup",
    description: null,
    location: null,
    start_time: "2026-03-05T09:30:00",
    end_time: "2026-03-05T09:45:00",
    all_day: false,
    is_recurring: true,
  },
];

describe("MonthGrid", () => {
  it("renders a 42-day grid with occurrences on their day", () => {
    wrap(<MonthGrid anchor={new Date(2026, 2, 15)} occurrences={occurrences} />);
    expect(screen.getByTestId("calendar-month-grid")).toBeInTheDocument();
    expect(screen.getByTestId("calendar-day-2026-03-05")).toBeInTheDocument();
    expect(screen.getByTestId("calendar-occurrence-1")).toHaveTextContent("Kickoff");
    expect(screen.getByTestId("calendar-occurrence-2")).toHaveTextContent("Standup");
  });

  it("calls onSelectDay when a day cell is clicked", () => {
    const onSelectDay = vi.fn();
    wrap(
      <MonthGrid anchor={new Date(2026, 2, 15)} occurrences={[]} onSelectDay={onSelectDay} />,
    );
    fireEvent.click(screen.getByTestId("calendar-day-2026-03-10"));
    expect(onSelectDay).toHaveBeenCalledTimes(1);
  });

  it("calls onSelectOccurrence without triggering onSelectDay", () => {
    const onSelectDay = vi.fn();
    const onSelectOccurrence = vi.fn();
    wrap(
      <MonthGrid
        anchor={new Date(2026, 2, 15)}
        occurrences={occurrences}
        onSelectDay={onSelectDay}
        onSelectOccurrence={onSelectOccurrence}
      />,
    );
    fireEvent.click(screen.getByTestId("calendar-occurrence-1"));
    expect(onSelectOccurrence).toHaveBeenCalledWith(occurrences[0]);
    expect(onSelectDay).not.toHaveBeenCalled();
  });
});
