import { elapsedToMinutes } from "@/lib/phoneCall";

/** Value of `PhoneTicketFields` (New-ticket page, phone mode). */
export type PhoneTicketFieldsValue = {
  ownerId: number | null;
  responsibleId: number | null;
  typeId: number | null;
  serviceId: number | null;
  slaId: number | null;
  /** `datetime-local` value for a pending initial state. */
  pendingAt: string;
  /** Minutes typed by the agent; `null` = follow the call timer. */
  timeUnits: string | null;
  dfValues: Record<string, string[]>;
};

export const EMPTY_PHONE_TICKET_FIELDS: PhoneTicketFieldsValue = {
  ownerId: null,
  responsibleId: null,
  typeId: null,
  serviceId: null,
  slaId: null,
  pendingAt: "",
  timeUnits: null,
  dfValues: {},
};

/** Minutes to book: typed value, else the timer rounded up. */
export function bookedMinutes(value: PhoneTicketFieldsValue, elapsed: number): number | null {
  const raw = value.timeUnits ?? String(elapsedToMinutes(elapsed));
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : null;
}
