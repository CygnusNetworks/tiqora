import { ApiClient } from "@tiqora/api-client";

let redirecting = false;

function goPortalLogin() {
  if (redirecting) return;
  if (typeof window === "undefined") return;
  const path = window.location.pathname + window.location.search;
  if (path.startsWith("/portal/login")) return;
  redirecting = true;
  const next = encodeURIComponent(path || "/portal");
  window.location.assign(`/portal/login?next=${next}`);
}

/** Customer-portal API client — separate session cookie, separate 401 handling. */
export const portalApi = new ApiClient({
  baseUrl: "",
  onUnauthorized: goPortalLogin,
});

export { ApiError } from "@tiqora/api-client";
export type {
  CustomerMe,
  TicketListItem,
  PaginatedTickets,
  TicketDetail,
  ArticleListItem,
  KbSearchHit,
  KbSearchResponse,
  PortalArticleOut,
  PortalReplyRequest,
  PortalReplyResponse,
  PortalTicketCreateRequest,
  PortalTicketCreateResponse,
  PortalAttachmentUploadResponse,
} from "@tiqora/api-client";
