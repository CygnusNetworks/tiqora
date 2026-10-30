import { beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import type { ScheduleItem } from "@/lib/reportSchedulesApi";
import { ScheduleReportsPicker } from "./ScheduleReportsPicker";

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

describe("ScheduleReportsPicker", () => {
  it("names a built-in missing from the catalogue by its title key, not its raw key", () => {
    const key = "builtin.itsm_changes_by_status";
    const view = { report_key: key, title: null, title_key: "reports.builtin.itsm_changes_by_status.title", available: true } as unknown as ScheduleItem;
    render(<ScheduleReportsPicker value={[{ report_key: key }]} onChange={vi.fn()} catalogue={[]} views={[view]} />);
    const row = screen.getByTestId("schedule-reports");
    expect(row).toHaveTextContent(i18n.t(view.title_key as string));
    expect(row).not.toHaveTextContent(key);
  });
});
