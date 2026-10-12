import { describe, expect, it } from "vitest";
import type { ReportSchedule } from "@/lib/reportSchedulesApi";
import { emptyForm, fromSchedule, sendsToOthers, toRequest, validateForm } from "./scheduleForm";

const DEFAULTS = { ownerId: 52120, reportKey: "builtin.volume", timeZone: "Europe/Berlin" };

const SCHEDULE_OUT: ReportSchedule = {
  id: 7,
  name: "Wochenübersicht",
  owner: { user_id: 52120, name: "Erika Beispiel" },
  is_owner: true,
  can_edit: true,
  items: [
    {
      report_key: "builtin.volume",
      title: null,
      title_key: "reports.builtin.volume.title",
      available: true,
      problem: null,
      overrides: { time_range: { kind: "relative", count: 7, unit: "day", include_current: false } },
    },
  ],
  format: "xlsx",
  recurrence: { kind: "weekly", time: "07:30", weekdays: [5, 1], workdays_only: false },
  recurrence_text: "Jeden Montag und Freitag um 07:30",
  time_zone: "Europe/Berlin",
  language: "de",
  subject: "Betreff",
  body: "Text",
  only_if_nonempty: true,
  active: true,
  paused_reason: null,
  next_run_at: "2026-10-19T05:30:00Z",
  last_run_at: null,
  last_status: null,
  failure_count: 0,
  recipients: [
    { kind: "user", id: 52120, email: null, label: "Erika Beispiel", problem: null },
    { kind: "group", id: 3, email: null, label: "Support", problem: null },
    { kind: "role", id: 4, email: null, label: "Leitung", problem: null },
    { kind: "email", id: null, email: "extern@example.org", label: "extern@example.org", problem: null },
  ],
  version: 4,
  changed_at: "2026-10-10T09:00:00Z",
} as ReportSchedule;

const SCHEDULE_IN_WITH_VERSION = {
  name: "Wochenübersicht",
  items: [
    {
      report_key: "builtin.volume",
      overrides: { time_range: { kind: "relative", count: 7, unit: "day", include_current: false } },
    },
  ],
  format: "xlsx",
  recurrence: { kind: "weekly", time: "07:30", weekdays: [1, 5], workdays_only: false },
  time_zone: "Europe/Berlin",
  language: "de",
  subject: "Betreff",
  body: "Text",
  only_if_nonempty: true,
  active: true,
  recipients: { user_ids: [52120], group_ids: [3], role_ids: [4], emails: ["extern@example.org"] },
  version: 4,
};

const NONE = { canShare: false, externalAllowed: false };

describe("scheduleForm", () => {
  it("round-trips a schedule", () => {
    expect(toRequest(fromSchedule(SCHEDULE_OUT))).toEqual(SCHEDULE_IN_WITH_VERSION);
  });

  it("sends no version for a new schedule", () => {
    expect(toRequest(emptyForm(DEFAULTS))).not.toHaveProperty("version");
  });

  it("defaults recipients to the owner and weekly Monday 07:00", () => {
    const f = emptyForm(DEFAULTS);
    expect(f.recipients.user_ids).toEqual([52120]);
    expect(f.recurrence).toEqual({
      kind: "weekly",
      time: "07:00",
      weekdays: [1],
      workdays_only: false,
      day_of_month: null,
      month: null,
    });
    expect(f.items).toEqual([{ report_key: "builtin.volume", overrides: {} }]);
  });

  it("validates", () => {
    const f = { ...emptyForm(DEFAULTS), name: " ", items: [] };
    expect(validateForm(f, NONE)).toMatchObject({
      name: "reports.schedules.errors.name",
      items: "reports.schedules.errors.items",
    });
    const g = { ...emptyForm(DEFAULTS), recipients: { ...emptyForm(DEFAULTS).recipients, group_ids: [3] } };
    expect(validateForm(g, NONE).recipients).toBe("reports.schedules.errors.shareRight");
    expect(validateForm(g, { canShare: true, externalAllowed: false }).recipients).toBeUndefined();
    expect(validateForm({ ...emptyForm(DEFAULTS), name: "x" }, NONE)).toEqual({});
  });

  it("checks e-mail permission and syntax", () => {
    const base = emptyForm(DEFAULTS);
    const withMail = (m: string) => ({ ...base, name: "x", recipients: { ...base.recipients, emails: [m] } });
    expect(validateForm(withMail("a@example.org"), { canShare: true, externalAllowed: false }).recipients).toBe(
      "reports.schedules.errors.externalNotAllowed",
    );
    expect(validateForm(withMail("kaputt"), { canShare: true, externalAllowed: true }).recipients).toBe(
      "reports.schedules.errors.email",
    );
    expect(validateForm(withMail("a@example.org"), { canShare: true, externalAllowed: true })).toEqual({});
  });

  it("requires the fields of the recurrence kind", () => {
    const base = { ...emptyForm(DEFAULTS), name: "x" };
    const rec = (r: Partial<typeof base.recurrence>) => ({ ...base, recurrence: { ...base.recurrence, ...r } });
    expect(validateForm(rec({ weekdays: [] }), NONE).weekdays).toBe("reports.schedules.errors.weekdays");
    expect(validateForm(rec({ kind: "monthly" }), NONE).day_of_month).toBe("reports.schedules.errors.dayOfMonth");
    expect(validateForm(rec({ kind: "yearly", day_of_month: 3 }), NONE).month).toBe("reports.schedules.errors.month");
    expect(validateForm(rec({ time: "7:00" }), NONE).time).toBe("reports.schedules.errors.time");
  });

  it("strips fields that do not belong to the recurrence kind", () => {
    const f = {
      ...emptyForm(DEFAULTS),
      recurrence: { kind: "daily" as const, time: "06:00", weekdays: [1, 2], workdays_only: true, day_of_month: 3, month: 2 },
    };
    expect(toRequest(f).recurrence).toEqual({ kind: "daily", time: "06:00", workdays_only: true });
    const y = { ...f, recurrence: { ...f.recurrence, kind: "yearly" as const } };
    expect(toRequest(y).recurrence).toEqual({ kind: "yearly", time: "06:00", workdays_only: false, day_of_month: 3, month: 2 });
  });

  it("knows when somebody else would receive the report", () => {
    expect(sendsToOthers({ user_ids: [1], group_ids: [], role_ids: [], emails: [] }, 1)).toBe(false);
    expect(sendsToOthers({ user_ids: [1, 2], group_ids: [], role_ids: [], emails: [] }, 1)).toBe(true);
  });
});
