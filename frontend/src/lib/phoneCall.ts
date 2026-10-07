/**
 * Pure helpers for the phone-call composer (`PhoneCallDialog`) and the phone
 * mode of the New-ticket page: timer display, time units from a call's
 * duration, callback presets, dial links and the per-ticket draft.
 */
import type { StateRef } from "@tiqora/api-client";
import type { DialScheme, DynamicFieldDef, PhoneDirection } from "./phoneApi";
import {
  addDaysYmd,
  displayTimeZone,
  fromZonedInputValue,
  toZonedInputValue,
  ymdInZone,
  zonedWallTimeToUtc,
} from "./timeZone";

/** `mm:ss`, or `h:mm:ss` from one hour on. */
export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const pad = (n: number) => String(n).padStart(2, "0");
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${pad(minutes)}:${pad(seconds)}`;
}

/** A call's duration as bookable minutes — every started minute counts. */
export function elapsedToMinutes(totalSeconds: number): number {
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return 0;
  return Math.ceil(totalSeconds / 60);
}

export type CallbackPreset = "inOneHour" | "today16" | "tomorrow9";

export const CALLBACK_PRESETS: CallbackPreset[] = ["inOneHour", "today16", "tomorrow9"];

/** The moment a callback preset stands for, relative to *now*, with the wall
 * clock of `zone` (the agent's display zone). `null` for "today 16:00" once
 * that time has passed. */
export function callbackPresetDate(
  preset: CallbackPreset,
  now: Date = new Date(),
  zone: string = displayTimeZone(),
): Date | null {
  if (preset === "inOneHour") {
    const d = new Date(now.getTime());
    d.setSeconds(0, 0);
    return new Date(d.getTime() + 3_600_000);
  }
  const today = ymdInZone(now, zone);
  if (preset === "today16") {
    const d = zonedWallTimeToUtc(today, "16:00", zone);
    return d && d.getTime() > now.getTime() ? d : null;
  }
  return zonedWallTimeToUtc(addDaysYmd(today, 1), "09:00", zone);
}

/** `<input type="datetime-local">` value (wall time in `zone`, minutes). */
export function toLocalInputValue(d: Date, zone: string = displayTimeZone()): string {
  return toZonedInputValue(d, zone);
}

/** Next-state choice in the phone composers. */
export type PhoneNextState = "keep" | "default" | "callback";

/**
 * Znuny's per-direction default next state: outbound → "closed successful"
 * (else the first closed state), inbound → "open". `undefined` when the
 * catalogue has no such state.
 */
export function defaultStateFor(direction: PhoneDirection, states: StateRef[]): StateRef | undefined {
  if (direction === "outbound") {
    return (
      states.find((s) => s.name === "closed successful") ??
      states.find((s) => s.type_name.startsWith("closed"))
    );
  }
  return states.find((s) => s.name === "open") ?? states.find((s) => s.type_name === "open");
}

/** The state a "Rückruf" (callback) sets: the first `pending reminder`. */
export function callbackState(states: StateRef[]): StateRef | undefined {
  return (
    states.find((s) => s.name === "pending reminder") ??
    states.find((s) => s.type_name === "pending reminder")
  );
}

/** `tel:`/`sip:` link for a number as typed in the customer record. */
export function dialHref(number: string, scheme: DialScheme = "tel"): string {
  const trimmed = number.trim();
  const plus = trimmed.startsWith("+") ? "+" : "";
  const digits = trimmed.replace(/[^\d*#]/g, "");
  return `${scheme}:${plus}${digits}`;
}

export type PhoneDraft = {
  direction: PhoneDirection;
  subject: string;
  body: string;
  /** Seconds on the timer when the draft was written. */
  elapsed: number;
};

const draftKey = (ticketId: number) => `tiqora-phone-draft-${ticketId}`;

export function loadPhoneDraft(ticketId: number): PhoneDraft | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(draftKey(ticketId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PhoneDraft>;
    if (parsed.direction !== "inbound" && parsed.direction !== "outbound") return null;
    return {
      direction: parsed.direction,
      subject: typeof parsed.subject === "string" ? parsed.subject : "",
      body: typeof parsed.body === "string" ? parsed.body : "",
      elapsed: typeof parsed.elapsed === "number" && parsed.elapsed > 0 ? parsed.elapsed : 0,
    };
  } catch {
    return null;
  }
}

export function savePhoneDraft(ticketId: number, draft: PhoneDraft): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(draftKey(ticketId), JSON.stringify(draft));
  } catch {
    // private mode / quota — the draft is a convenience only
  }
}

export function clearPhoneDraft(ticketId: number): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(draftKey(ticketId));
  } catch {
    // ignore
  }
}

/** "Open the phone dialog on ticket N" — set before navigating to a ticket
 * (New-ticket caller lookup, click-to-call, the CTI call popup), read by the
 * ticket header once it mounts — or right away when that ticket is already
 * open (see `subscribePhoneCallRequests`). In memory only: a reload simply
 * opens the ticket. */
export type PhoneCallRequestIntent = {
  direction: PhoneDirection;
  number?: string | null;
  /** Epoch ms the call was answered (CTI popup) — the timer counts from here. */
  startedAt?: number | null;
  /** Epoch ms the call ended — the timer shows the fixed duration. */
  endedAt?: number | null;
};

const pendingIntents = new Map<number, PhoneCallRequestIntent>();
const requestListeners = new Set<(ticketId: number) => void>();

export function requestPhoneCall(ticketId: number, intent: PhoneCallRequestIntent): void {
  pendingIntents.set(ticketId, intent);
  for (const listener of requestListeners) listener(ticketId);
}

export function peekPhoneCallRequest(ticketId: number): PhoneCallRequestIntent | null {
  return pendingIntents.get(ticketId) ?? null;
}

export function consumePhoneCallRequest(ticketId: number): void {
  pendingIntents.delete(ticketId);
}

/** Notified with the ticket id on every `requestPhoneCall`. */
export function subscribePhoneCallRequests(listener: (ticketId: number) => void): () => void {
  requestListeners.add(listener);
  return () => {
    requestListeners.delete(listener);
  };
}

/**
 * Call-timer seed for a call that started (and maybe ended) elsewhere — the
 * CTI popup's answered/hangup times: seconds already elapsed, and whether the
 * timer keeps running. `null` without a start time.
 */
export function timerFromCall(
  startedAt: number | null | undefined,
  endedAt: number | null | undefined,
  now: number = Date.now(),
): { initialSeconds: number; autoStart: boolean } | null {
  if (!startedAt || !Number.isFinite(startedAt)) return null;
  const end = endedAt && Number.isFinite(endedAt) ? endedAt : now;
  return {
    initialSeconds: Math.max(0, Math.floor((end - startedAt) / 1000)),
    autoStart: !endedAt,
  };
}

/** `datetime-local` value (wall time in `zone`) → ISO string for the API
 * (`null` when blank). */
export function pendingIso(value: string, zone: string = displayTimeZone()): string | null {
  return fromZonedInputValue(value, zone)?.toISOString() ?? null;
}

/** Required dynamic fields without a value, by name. */
export function missingRequired(
  fields: DynamicFieldDef[],
  values: Record<string, string[]>,
): string[] {
  return fields
    .filter((f) => f.required && !(values[f.name] ?? []).some((v) => v.trim() !== ""))
    .map((f) => f.name);
}
