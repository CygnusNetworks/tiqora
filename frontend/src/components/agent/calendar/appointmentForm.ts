import type { AppointmentOut, RecurrenceIn } from "@/lib/api";

export type AppointmentFormValue = {
  calendar_id: number;
  title: string;
  description: string;
  location: string;
  start_time: string; // datetime-local value
  end_time: string; // datetime-local value
  all_day: boolean;
  recurrence: { type: RecurrenceIn["type"] | ""; interval: number; count: string; until: string };
};

function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function defaultFormValue(
  calendarId: number,
  start?: Date,
  end?: Date,
): AppointmentFormValue {
  const s = start ?? new Date();
  const e = end ?? new Date(s.getTime() + 60 * 60 * 1000);
  return {
    calendar_id: calendarId,
    title: "",
    description: "",
    location: "",
    start_time: toLocalInput(s.toISOString()),
    end_time: toLocalInput(e.toISOString()),
    all_day: false,
    recurrence: { type: "", interval: 1, count: "", until: "" },
  };
}

export function formValueFromAppointment(appt: AppointmentOut): AppointmentFormValue {
  return {
    calendar_id: appt.calendar_id,
    title: appt.title,
    description: appt.description ?? "",
    location: appt.location ?? "",
    start_time: toLocalInput(appt.start_time),
    end_time: toLocalInput(appt.end_time),
    all_day: appt.all_day,
    recurrence: {
      type: (appt.recur_type as RecurrenceIn["type"] | null) ?? "",
      interval: appt.recur_interval ?? 1,
      count: appt.recur_count ? String(appt.recur_count) : "",
      until: toLocalInput(appt.recur_until),
    },
  };
}
