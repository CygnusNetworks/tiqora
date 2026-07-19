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
  AuthMethodsOut,
  TOTPCodeIn,
  TOTPEnrollOut,
  TOTPStatusOut,
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
  CategoryOut,
  CategoryIn,
  CategoryUpdateIn,
  KbArticleOut,
  KbArticleIn,
  KbArticleUpdateIn,
  ArticleSummary,
  ArticleVersionOut,
  KbSearchHit,
  KbSearchResponse,
  UserOut,
  UserCreate,
  UserUpdate,
  GroupOut,
  GroupCreate,
  GroupUpdate,
  RoleOut,
  RoleCreate,
  RoleUpdate,
  QueueOut,
  QueueCreate,
  QueueUpdate,
  StateOut,
  StateCreate,
  StateUpdate,
  PriorityOut,
  PriorityCreate,
  PriorityUpdate,
  CustomerUserAdminOut,
  CustomerUserAdminCreate,
  CustomerUserAdminUpdate,
  CustomerCompanyOut,
  CustomerCompanyCreate,
  CustomerCompanyUpdate,
  SalutationOut,
  SalutationWrite,
  SalutationUpdate,
  SignatureOut,
  SignatureWrite,
  SignatureUpdate,
  StandardTemplateOut,
  StandardTemplateCreate,
  StandardTemplateUpdate,
  AutoResponseOut,
  AutoResponseCreate,
  AutoResponseUpdate,
  DynamicFieldOut,
  DynamicFieldCreate,
  DynamicFieldUpdate,
  WebhookOut,
  WebhookCreate,
  WebhookUpdate,
  PostmasterFilterOut,
  AclOut,
  GenericAgentJobOut,
} from "@tiqora/api-client";
