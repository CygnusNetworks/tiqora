import { ApiClient } from "@tiqora/api-client";

let redirecting = false;

function goLogin() {
  if (redirecting) return;
  if (typeof window === "undefined") return;
  const path = window.location.pathname + window.location.search;
  if (path.startsWith("/login")) return;
  redirecting = true;
  const next = encodeURIComponent(path || "/agent");
  window.location.assign(`/login?next=${next}`);
}

/** Shared API client — session cookie via credentials: include. */
export const api = new ApiClient({
  baseUrl: "",
  onUnauthorized: goLogin,
});

export { ApiError } from "@tiqora/api-client";
export type {
  UserMe,
  QueueNode,
  TicketListItem,
  PaginatedTickets,
  TicketDetail,
  ArticleListItem,
  ArticleBody,
  AttachmentMetaOut,
  HistoryEntry,
  SearchResponse,
  SearchHit,
} from "@tiqora/api-client";
