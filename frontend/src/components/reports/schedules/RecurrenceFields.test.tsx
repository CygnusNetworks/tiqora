import { useState } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { reportSchedulesApi } from "@/lib/reportSchedulesApi";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { RecurrenceFields } from "./RecurrenceFields";
import { recurrencePreviewDebounce, zoneSuffix } from "./scheduleUi";
import { emptyRecurrence, recurrenceErrors, type RecurrenceState } from "./scheduleForm";

function Harness({ initial = emptyRecurrence(), onValue }: { initial?: RecurrenceState; onValue?: (r: RecurrenceState) => void }) {
  const [value, setValue] = useState(initial);
  const [zone, setZone] = useState("Europe/Berlin");
  return (
    <RecurrenceFields
      value={value}
      onChange={(r) => {
        setValue(r);
        onValue?.(r);
      }}
      timeZone={zone}
      onTimeZoneChange={setZone}
      errors={recurrenceErrors(value)}
    />
  );
}

function renderFields(initial?: RecurrenceState, onValue?: (r: RecurrenceState) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <Harness initial={initial} onValue={onValue} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
  setDisplayTimeZone("Europe/Berlin");
});

beforeEach(() => {
  recurrencePreviewDebounce.ms = 10;
});

afterEach(() => {
  recurrencePreviewDebounce.ms = 400;
  vi.restoreAllMocks();
});

const PREVIEW = {
  text: "Jeden Montag um 07:00 Uhr",
  next_runs: ["2026-10-19T05:00:00Z", "2026-10-26T06:00:00Z", "2026-11-02T06:00:00Z", "2026-11-09T06:00:00Z", "2026-11-16T06:00:00Z"],
};

describe("RecurrenceFields", () => {
  it("renders the five next dates in local time", async () => {
    vi.spyOn(reportSchedulesApi, "previewRecurrence").mockResolvedValue(PREVIEW);
    renderFields();
    const items = within(await screen.findByTestId("recurrence-next-runs")).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    expect(items[0]).toHaveTextContent("07:00");
    expect(items[1]).toHaveTextContent("07:00"); // 06:00Z after the clocks went back is 07:00 CET
  });

  it("asks the server with only the fields of the kind, debounced", async () => {
    const preview = vi.spyOn(reportSchedulesApi, "previewRecurrence").mockResolvedValue(PREVIEW);
    renderFields();
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId("recurrence-kind-daily"));
    fireEvent.change(screen.getByTestId("recurrence-time"), { target: { value: "06:15" } });
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(2));
    expect(preview.mock.calls[1][0]).toEqual({ kind: "daily", time: "06:15", workdays_only: false });
    expect(preview.mock.calls[1][1]).toBe("Europe/Berlin");
  });

  it("toggles weekdays and asks for at least one", async () => {
    vi.spyOn(reportSchedulesApi, "previewRecurrence").mockResolvedValue(PREVIEW);
    const seen: RecurrenceState[] = [];
    renderFields(undefined, (r) => seen.push(r));
    fireEvent.click(screen.getByTestId("recurrence-weekday-5"));
    expect(seen.at(-1)?.weekdays).toEqual([1, 5]);
    fireEvent.click(screen.getByTestId("recurrence-weekday-1"));
    fireEvent.click(screen.getByTestId("recurrence-weekday-5"));
    expect(await screen.findByTestId("recurrence-error-weekdays")).toHaveTextContent("Bitte wählen Sie mindestens einen Wochentag.");
  });

  it("shows day of month with its hint, and the month for yearly", () => {
    vi.spyOn(reportSchedulesApi, "previewRecurrence").mockResolvedValue(PREVIEW);
    renderFields({ ...emptyRecurrence(), kind: "monthly", day_of_month: 31 });
    expect(screen.getByTestId("recurrence-day-of-month")).toHaveValue(31);
    expect(screen.getByText("In kürzeren Monaten am letzten Tag.")).toBeInTheDocument();
    expect(screen.queryByTestId("recurrence-month")).toBeNull();
    fireEvent.click(screen.getByTestId("recurrence-kind-yearly"));
    expect(screen.getByTestId("recurrence-month")).toBeInTheDocument();
  });

  it("adds the zone to a time only when it differs from the displayed one", () => {
    expect(zoneSuffix("07:00", "Europe/Berlin")).toBe("07:00");
    expect(zoneSuffix("07:00", "America/New_York")).toBe("07:00 (America/New_York)");
  });
});
