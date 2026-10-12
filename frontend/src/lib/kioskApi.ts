/**
 * Wallboard (kiosk) client for `backend/src/tiqora/api/v1/kiosk.py`.
 *
 * The kiosk is the one unauthenticated surface: it must not use the shared `api` client (cookie
 * session, 401 redirect to the login). The link's secret travels in the `X-Tiqora-Kiosk` header
 * (the SPA reads it from the URL fragment, so it never reaches a server log or a referrer), never
 * in the URL, and no credentials are sent.
 *
 * 404 (every failure of the link: unknown, revoked, expired, wrong dashboard) is final and shows
 * "link invalid"; 429, 503 and network failures are retryable, so a TV keeps its last picture.
 */
import type { Schemas } from "@tiqora/api-client";
import { ApiError } from "./api";

export type KioskBoard = Schemas["KioskBoardOut"];
export type KioskDashboard = Schemas["KioskDashboardOut"];
export type KioskWidget = Schemas["KioskWidgetOut"];
export type KioskData = Schemas["DashboardDataOut"];

export const KIOSK_HEADER = "X-Tiqora-Kiosk";
const BASE = "/api/v1/kiosk";
const DEFAULT_RETRY_SECONDS = 30;

/** The link is unknown, revoked, expired or does not include the dashboard. */
export class KioskLinkInvalidError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "KioskLinkInvalidError";
  }
}

/** Rate limited (429), unavailable (503) or no network (`status` 0): try again later. */
export class KioskRetryableError extends Error {
  readonly status: number;
  readonly retryAfterSeconds: number;

  constructor(message: string, status: number, retryAfterSeconds: number) {
    super(message);
    this.name = "KioskRetryableError";
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function messageOf(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  const message = (detail as { message?: unknown } | null)?.message;
  return typeof message === "string" ? message : fallback;
}

function retryAfter(res: Response): number {
  const seconds = Number(res.headers.get("Retry-After"));
  return Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : DEFAULT_RETRY_SECONDS;
}

async function get<T>(path: string, token: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "GET",
      credentials: "omit",
      headers: { [KIOSK_HEADER]: token, Accept: "application/json" },
      cache: "no-store",
      referrerPolicy: "no-referrer",
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new KioskRetryableError("Keine Verbindung.", 0, DEFAULT_RETRY_SECONDS);
  }
  if (res.ok) return (await res.json()) as T;
  const body: unknown = await res.json().catch(() => null);
  const message = messageOf(body, `HTTP ${res.status}`);
  if (res.status === 404) throw new KioskLinkInvalidError(message);
  if (res.status === 429 || res.status === 503) {
    throw new KioskRetryableError(message, res.status, retryAfter(res));
  }
  throw new ApiError(res.status, body, path);
}

export const kioskApi = {
  board(token: string, signal?: AbortSignal) {
    return get<KioskBoard>(`${BASE}/board`, token, signal);
  },
  data(token: string, dashboardId: number, signal?: AbortSignal) {
    return get<KioskData>(`${BASE}/dashboards/${dashboardId}/data`, token, signal);
  },
};
