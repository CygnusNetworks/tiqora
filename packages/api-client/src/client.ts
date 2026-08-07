/**
 * Thin typed fetch wrapper for Tiqora REST /api/v1.
 *
 * - credentials: 'include' for session cookies
 * - normalises errors to ApiError
 * - optional 401 → login redirect handler
 */

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type UserMe = Schemas["UserMe"];
export type LoginRequest = Schemas["LoginRequest"];
export type LoginResponse = Schemas["LoginResponse"];
export type AuthMethodsOut = Schemas["AuthMethodsOut"];
export type TOTPCodeIn = Schemas["TOTPCodeIn"];
export type TOTPEnrollOut = Schemas["TOTPEnrollOut"];
export type TOTPStatusOut = Schemas["TOTPStatusOut"];
export type PasskeyOut = Schemas["PasskeyOut"];
export type PasskeyStatusOut = Schemas["PasskeyStatusOut"];
export type PasskeyRegisterFinishIn = Schemas["PasskeyRegisterFinishIn"];
export type PasskeyAuthenticateFinishIn = Schemas["PasskeyAuthenticateFinishIn"];
/** WebAuthn PublicKeyCredential*Options JSON (py_webauthn / simplewebauthn). */
export type PasskeyOptionsJSON = Record<string, unknown>;
export type QueueNode = Schemas["QueueNode"];
export type QueueCounts = Schemas["QueueCounts"];
export type TicketListItem = Schemas["TicketListItem"];
export type PaginatedTickets = Schemas["PaginatedTickets"];
export type MyTicketCounts = Schemas["MyTicketCounts"];
// Hand-written (see the Stats block below for why we don't regenerate
// schema.d.ts): mirrors the DashboardSummary model in tiqora/api/v1/tickets.py.
export type DashboardSummary = {
  my_open: number;
  my_new: number;
  unowned_new: number;
  escalated: number;
};
export type TicketDetail = Schemas["TicketDetail"];
export type TicketPermissions = Schemas["TicketPermissions"];
export type ArticleListItem = Schemas["ArticleListItem"];
export type ArticleBody = Schemas["ArticleBody"];
export type AttachmentMetaOut = Schemas["AttachmentMetaOut"];
export type HistoryEntry = Schemas["HistoryEntry"];
export type CustomerUserOut = Schemas["CustomerUserOut"];
export type SearchHit = Schemas["SearchHit"];
export type SearchResponse = Schemas["SearchResponse"];
export type SimilarTicketItem = Schemas["SimilarTicketItem"];
export type SimilarTicketsOut = Schemas["SimilarTicketsOut"];
export type CustomerCompanyRefOut = Schemas["CustomerCompanyRefOut"];
export type CustomerContactRefOut = Schemas["CustomerContactRefOut"];
export type CustomerSearchOut = Schemas["CustomerSearchOut"];
export type DynamicFieldValueOut = Schemas["DynamicFieldValueOut"];
export type PresenceIn = Schemas["PresenceIn"];
export type PresenceEntry = Schemas["PresenceEntry"];
/** Global online-agent presence (``GET /agents/online``). */
export type OnlineAgentOut = Schemas["OnlineAgentOut"];
export type ArticleCreateRequest = Schemas["ArticleCreateRequest"];
export type ArticleCreateResponse = Schemas["ArticleCreateResponse"];
export type ReplyDraftOut = Schemas["ReplyDraftOut"];
export type TemplateOut = Schemas["TemplateOut"];
export type MutationRequest = Schemas["MutationRequest"];
export type MergeRequest = Schemas["MergeRequest"];
export type TicketCreateRequest = Schemas["TicketCreateRequest"];
export type TicketCreateResponse = Schemas["TicketCreateResponse"];
export type ForwardRequest = Schemas["ForwardRequest"];
export type BounceRequest = Schemas["BounceRequest"];
export type SplitRequest = Schemas["SplitRequest"];
export type TicketLinkTargetOut = Schemas["TicketLinkTargetOut"];
export type TicketLinkCreateRequest = Schemas["TicketLinkCreateRequest"];

// ── Portal ────────────────────────────────────────────────────────────────
export type CustomerMe = Schemas["CustomerMe"];
export type CustomerLoginResponse = Schemas["CustomerLoginResponse"];
export type PortalArticleOut = Schemas["PortalArticleOut"];
export type PortalReplyRequest = Schemas["PortalReplyRequest"];
export type PortalReplyResponse = Schemas["PortalReplyResponse"];
export type PortalTicketCreateRequest = Schemas["PortalTicketCreateRequest"];
export type PortalTicketCreateResponse = Schemas["PortalTicketCreateResponse"];
export type PortalAttachmentUploadResponse = Schemas["PortalAttachmentUploadResponse"];
export type KbSearchHit = Schemas["KbSearchHit"];
export type KbSearchResponse = Schemas["KbSearchResponse"];

// ── Knowledge base (agent) ───────────────────────────────────────────────
export type CategoryOut = Schemas["CategoryOut"];
export type CategoryIn = Schemas["CategoryIn"];
export type CategoryUpdateIn = Schemas["CategoryUpdateIn"];
export type KbArticleOut = Schemas["ArticleOut"];
export type KbArticleIn = Schemas["ArticleIn"];
export type KbArticleUpdateIn = Schemas["ArticleUpdateIn"];
export type ArticleSummary = Schemas["ArticleSummary"];
export type ArticleVersionOut = Schemas["ArticleVersionOut"];
export type AssignableGroup = Schemas["AssignableGroup"];
// Hand-typed until the /kb/tags endpoint lands in the regenerated schema —
// shape mirrors backend KbTagOut exactly.
export type KbTagOut = { name: string; article_count: number };
export type KbAttachmentOut = Schemas["AttachmentOut"];
export type KnowledgeArticle = Schemas["KnowledgeArticle"];
export type KnowledgeBundle = Schemas["KnowledgeBundle"];

// ── Admin ─────────────────────────────────────────────────────────────────
/** Validity filter for admin resource lists; defaults to hiding invalid rows. */
export type AdminValidFilter = "valid" | "invalid" | "all";

/** Query params for a paginated admin list. */
export type AdminListParams = {
  page?: number;
  pageSize?: number;
  valid?: AdminValidFilter;
  /** Optional server-side substring search (customer users / companies). */
  search?: string;
  /** Treat `search` as a case-insensitive regex (customer users only). */
  regex?: boolean;
  /** Optional allowlisted sort column key (customer users). */
  sort?: string;
  /** Sort direction; only sent when `sort` is set. */
  order?: "asc" | "desc";
};

/** Paginated envelope returned by every admin resource list endpoint. */
export type AdminPage<Out> = {
  items: Out[];
  total: number;
  page: number;
  page_size: number;
};

export type UserOut = Schemas["UserOut"];
export type UserCreate = Schemas["UserCreate"];
export type UserUpdate = Schemas["UserUpdate"];
export type GroupOut = Schemas["GroupOut"];
export type GroupCreate = Schemas["GroupCreate"];
export type GroupUpdate = Schemas["GroupUpdate"];
export type GroupAssignment = Schemas["GroupAssignment"];
export type RoleOut = Schemas["RoleOut"];
export type RoleCreate = Schemas["RoleCreate"];
export type RoleUpdate = Schemas["RoleUpdate"];
export type RoleAssignment = Schemas["RoleAssignment"];
export type GroupRoleAssignment = Schemas["GroupRoleAssignment"];
export type EffectivePermissionsOut = Schemas["EffectivePermissionsOut"];
export type EffectiveGroupPermission = Schemas["EffectiveGroupPermission"];
export type EffectiveQueuePermission = Schemas["EffectiveQueuePermission"];
export type EffectivePermissionSource = Schemas["EffectivePermissionSource"];
export type UserLanguageOut = Schemas["UserLanguageOut"];
export type UserDeletableOut = Schemas["UserDeletableOut"];
export type UserReference = Schemas["UserReference"];
export type UserLanguageUpdate = Schemas["UserLanguageUpdate"];
export type QueueOut = Schemas["QueueOut"];
export type QueueCreate = Schemas["QueueCreate"];
export type QueueUpdate = Schemas["QueueUpdate"];
export type StateOut = Schemas["StateOut"];
export type StateCreate = Schemas["StateCreate"];
export type StateUpdate = Schemas["StateUpdate"];
export type PriorityOut = Schemas["PriorityOut"];
export type PriorityCreate = Schemas["PriorityCreate"];
export type PriorityUpdate = Schemas["PriorityUpdate"];
export type TicketTypeOut = Schemas["TicketTypeOut"];
export type TicketTypeCreate = Schemas["TicketTypeCreate"];
export type TicketTypeUpdate = Schemas["TicketTypeUpdate"];
export type ServiceOut = Schemas["ServiceOut"];
export type ServiceCreate = Schemas["ServiceCreate"];
export type ServiceUpdate = Schemas["ServiceUpdate"];
export type SlaOut = Schemas["SlaOut"];
export type SlaCreate = Schemas["SlaCreate"];
export type SlaUpdate = Schemas["SlaUpdate"];
export type SystemAddressCreate = Schemas["SystemAddressCreate"];
export type SystemAddressUpdate = Schemas["SystemAddressUpdate"];
export type NotificationEventOut = Schemas["NotificationEventOut"];
export type NotificationEventWrite = Schemas["NotificationEventWrite"];
export type NotificationEventUpdate = Schemas["NotificationEventUpdate"];
export type NotificationMessageIn = Schemas["NotificationMessageIn"];
export type GenericAgentJobWrite = Schemas["GenericAgentJobWrite"];
export type GenericAgentJobUpdate = Schemas["GenericAgentJobUpdate"];
export type MentionOut = Schemas["MentionOut"];
export type MentionCreate = Schemas["MentionCreate"];
export type TimeAccountingOut = Schemas["TimeAccountingOut"];
export type TimeAccountingCreate = Schemas["TimeAccountingCreate"];
/** Hand-written: cross-ticket time-accounting report row (see GET /tickets/time-accounting). */
export type TimeAccountingReportEntry = {
  id: number;
  ticket_id: number;
  ticket_tn?: string | null;
  ticket_title?: string | null;
  article_id?: number | null;
  time_unit: number;
  create_time?: string | null;
  create_by: number;
  create_by_login?: string | null;
};
export type TimeAccountingReportOut = {
  items: TimeAccountingReportEntry[];
  total_units: number;
  offset: number;
  limit: number;
};
export type TypeRef = Schemas["TypeRefOut"];
export type ServiceRef = Schemas["ServiceRefOut"];
export type SlaRef = Schemas["SlaRefOut"];
export type CustomerUserAdminOut = Schemas["CustomerUserAdminOut"];
export type CustomerUserAdminCreate = Schemas["CustomerUserAdminCreate"];
export type CustomerUserAdminUpdate = Schemas["CustomerUserAdminUpdate"];
export type CustomerUserBulkUpdate = Schemas["CustomerUserBulkUpdate"];
export type CustomerUserBulkUpdateResult = Schemas["CustomerUserBulkUpdateResult"];
export type CustomerCompanyOut = Schemas["CustomerCompanyOut"];
export type CustomerCompanyCreate = Schemas["CustomerCompanyCreate"];
export type CustomerCompanyUpdate = Schemas["CustomerCompanyUpdate"];
export type SalutationOut = Schemas["SalutationOut"];
export type SalutationWrite = Schemas["SalutationWrite"];
export type SalutationUpdate = Schemas["SalutationUpdate"];
export type SignatureOut = Schemas["SignatureOut"];
export type SignatureWrite = Schemas["SignatureWrite"];
export type SignatureUpdate = Schemas["SignatureUpdate"];
export type StandardTemplateOut = Schemas["StandardTemplateOut"];
export type StandardTemplateCreate = Schemas["StandardTemplateCreate"];
export type StandardTemplateUpdate = Schemas["StandardTemplateUpdate"];
export type TemplateEditorsOut = Schemas["TemplateEditorsOut"];
export type TemplateEditorsUpdate = Schemas["TemplateEditorsUpdate"];
// Hand-written (do not regenerate schema.d.ts): standard_attachment master +
// template/attachment + customer-user/group assignment editors.
export type StandardAttachmentOut = {
  id: number;
  name: string;
  content_type: string;
  /** Base64-encoded blob body. */
  content: string;
  filename: string;
  comments: string | null;
  valid_id: number;
  create_time: string;
  change_time: string;
  /** How many templates link this attachment (list responses). */
  assigned_template_count?: number;
};
export type StandardAttachmentCreate = {
  name: string;
  content_type: string;
  /** Base64-encoded blob body. */
  content: string;
  filename: string;
  comments?: string | null;
  valid_id?: number;
};
export type StandardAttachmentUpdate = {
  name?: string | null;
  content_type?: string | null;
  content?: string | null;
  filename?: string | null;
  comments?: string | null;
  valid_id?: number | null;
};
/** Slim attachment row for the template↔attachments editor (no blob). */
export type AttachmentRefOut = {
  id: number;
  name: string;
  filename: string;
  content_type: string;
};
export type TemplateAttachmentsReplace = {
  attachment_ids: number[];
};
/** Customer-user ↔ group grant (group_customer_user; login string identity). */
export type CustomerUserGroupAssignment = {
  group_id: number;
  permission_key: "ro" | "rw";
  permission_value?: number;
};
export type AutoResponseOut = Schemas["AutoResponseOut"];
export type AutoResponseCreate = Schemas["AutoResponseCreate"];
export type AutoResponseUpdate = Schemas["AutoResponseUpdate"];
export type DynamicFieldOut = Schemas["DynamicFieldOut"];
export type DynamicFieldCreate = Schemas["DynamicFieldCreate"];
export type DynamicFieldUpdate = Schemas["DynamicFieldUpdate"];
export type WebhookOut = Schemas["WebhookOut"];
export type WebhookCreate = Schemas["WebhookCreate"];
export type WebhookUpdate = Schemas["WebhookUpdate"];
export type ApiKeyOut = Schemas["ApiKeyOut"];
export type ApiKeyCreate = Schemas["ApiKeyCreate"];
export type ApiKeyUpdate = Schemas["ApiKeyUpdate"];
/** Create response includes the plaintext `key` exactly once. */
export type ApiKeyCreated = Schemas["ApiKeyCreated"];
export type AuthConfigAgentOut = Schemas["AuthConfigAgentOut"];
export type AuthConfigUpdate = Schemas["AuthConfigUpdate"];
export type AuthConfigGlobalOut = Schemas["AuthConfigGlobalOut"];
export type AuthConfigGlobalUpdate = Schemas["AuthConfigGlobalUpdate"];
// Placeholder variables (regenerated into schema.d.ts from openapi.json).
export type QueueVariableOut = Schemas["QueueVariableOut"];
export type QueueVariableCreate = Schemas["QueueVariableCreate"];
export type QueueVariableUpdate = Schemas["QueueVariableUpdate"];
export type PhysicalQueueVariableOut = Schemas["PhysicalQueueVariableOut"];
export type PlaceholderFieldOut = Schemas["PlaceholderFieldOut"];
export type PlaceholderFieldCreate = Schemas["PlaceholderFieldCreate"];
export type PlaceholderFieldUpdate = Schemas["PlaceholderFieldUpdate"];
export type DaemonServiceOut = Schemas["DaemonServiceOut"];
export type DaemonListOut = Schemas["DaemonListOut"];
export type SystemInfoOut = Schemas["SystemInfoOut"];
export type DaemonUpdate = Schemas["DaemonUpdate"];
// Hand-written until openapi.json is regenerated (schemas also appear there).
export type MailSecurity = "none" | "starttls" | "ssl";
export type MailAuthType = "none" | "password" | "oauth2_token";
export type MailOutboundOut = {
  enabled: boolean;
  host: string;
  port: number;
  security: MailSecurity;
  auth_type: MailAuthType;
  auth_user: string;
  has_password: boolean;
  oauth2_token_config_name?: string;
  from_default: string;
  timeout_seconds: number;
  change_time?: string | null;
  change_by?: number | null;
};

/** Znuny-compatible OAuth2 mail token config (legacy oauth2_token_config). */
export type OAuth2TokenConfigOut = {
  id: number;
  name: string;
  config: Record<string, unknown>;
  client_id: string;
  has_client_secret: boolean;
  scope: string;
  valid: boolean;
  token_status: string;
  token_expiration_date?: string | null;
  refresh_token_expiration_date?: string | null;
  has_token: boolean;
  has_refresh_token: boolean;
  error_message: string;
  create_time?: string | null;
  create_by?: number | null;
  change_time?: string | null;
  change_by?: number | null;
  redirect_uri: string;
};
export type OAuth2TokenConfigCreate = {
  name: string;
  config?: Record<string, unknown>;
  valid?: boolean;
  client_id?: string | null;
  client_secret?: string | null;
  scope?: string | null;
  template_id?: string | null;
};
export type OAuth2TokenConfigUpdate = {
  name?: string | null;
  config?: Record<string, unknown> | null;
  valid?: boolean | null;
  client_id?: string | null;
  client_secret?: string | null;
  scope?: string | null;
};
export type OAuth2AuthorizeUrlOut = {
  url: string;
  redirect_uri: string;
  state: string;
};
export type OAuth2ProviderTemplateOut = {
  id: string;
  name: string;
  config: Record<string, unknown>;
};

export type MailAccountType = "IMAP" | "IMAPS" | "POP3" | "POP3S";
export type MailAccountAuthType = "password" | "oauth2_token";
export type MailAccountOut = {
  id: number;
  login: string;
  host: string;
  account_type: string;
  queue_id: number;
  trusted: boolean;
  imap_folder?: string | null;
  authentication_type: string;
  oauth2_token_config_id?: number | null;
  comments?: string | null;
  valid: boolean;
  has_password: boolean;
  create_time?: string | null;
  change_time?: string | null;
};
export type MailAccountCreate = {
  login: string;
  pw?: string | null;
  host: string;
  account_type?: MailAccountType;
  queue_id: number;
  trusted?: boolean;
  imap_folder?: string | null;
  authentication_type?: MailAccountAuthType;
  oauth2_token_config_id?: number | null;
  comments?: string | null;
  valid?: boolean;
};
export type MailAccountUpdate = {
  login?: string | null;
  pw?: string | null;
  host?: string | null;
  account_type?: MailAccountType | null;
  queue_id?: number | null;
  trusted?: boolean | null;
  imap_folder?: string | null;
  authentication_type?: MailAccountAuthType | null;
  oauth2_token_config_id?: number | null;
  comments?: string | null;
  valid?: boolean | null;
};
export type SubjectFormat = "Left" | "Right" | "None";
export type SubjectHookZnunyOut = {
  hook: string;
  divider: string;
  subject_format: string;
};
export type SubjectHookOverridesOut = {
  enabled?: boolean | null;
  hook?: string | null;
  divider?: string | null;
  subject_format?: string | null;
};
export type SubjectConfigOut = {
  enabled: boolean;
  hook: string;
  divider: string;
  subject_format: string;
  overrides: SubjectHookOverridesOut;
  znuny: SubjectHookZnunyOut;
};
export type SubjectConfigUpdate = {
  enabled?: boolean | null;
  hook?: string | null;
  divider?: string | null;
  subject_format?: string | null;
};
export type MailOutboundUpdate = {
  enabled?: boolean | null;
  host?: string | null;
  port?: number | null;
  security?: MailSecurity | null;
  auth_type?: MailAuthType | null;
  auth_user?: string | null;
  /** Write-only; omit or empty keeps the stored password. */
  auth_password?: string | null;
  oauth2_token_config_name?: string | null;
  from_default?: string | null;
  timeout_seconds?: number | null;
};
export type MailOutboundTestIn = {
  to_address?: string | null;
};
export type MailOutboundTestOut = {
  ok: boolean;
  message: string;
  detail?: string | null;
};
export type MailLogDirection = "in" | "out";
export type MailLogStatus = "queued" | "sent" | "failed" | "received" | "filtered";
export type MailLogOut = {
  id: number;
  created_at: string;
  direction: string;
  status: string;
  from_addr: string;
  to_addr: string;
  cc_addr?: string | null;
  subject: string;
  message_id?: string | null;
  ticket_id?: number | null;
  article_id?: number | null;
  queue?: string | null;
  smtp_code?: number | null;
  detail?: string | null;
  duration_ms?: number | null;
};
export type MailLogListParams = {
  page?: number;
  pageSize?: number;
  direction?: MailLogDirection | null;
  status?: MailLogStatus | null;
  q?: string | null;
  /** ISO datetime lower bound (query param ``from``). */
  from?: string | null;
  /** ISO datetime upper bound (query param ``to``). */
  to?: string | null;
};
// GDPR erasure (admin) — types mirror admin/schemas.py; also regenerated into schema.d.ts.
export type ErasureMode = "anonymize" | "delete";
export type ErasureSelectorIn = {
  logins?: string[];
  customer_ids?: string[];
  login_regex?: string | null;
  login_regex_negate?: boolean;
  customer_id_regex?: string | null;
  customer_id_regex_negate?: boolean;
  email_regex?: string | null;
  email_regex_negate?: boolean;
  changed_before?: string | null;
  changed_after?: string | null;
  activity?: string | null;
  valid_id?: number | null;
};
export type GdprErasurePreviewRequest = {
  selector: ErasureSelectorIn;
  mode?: ErasureMode;
  /** delete mode only: also hard-delete the customer's tickets + FK children. */
  delete_tickets?: boolean;
};
export type GdprResolvedCustomerOut = {
  id: number;
  login: string;
  email: string;
  customer_id: string;
};
export type GdprSampleRowOut = {
  table: string;
  id: unknown;
  summary: string;
};
export type GdprErasurePreviewOut = {
  mode: string;
  customers: GdprResolvedCustomerOut[];
  counts: Record<string, number>;
  sample: GdprSampleRowOut[];
  columns_changed: Record<string, string[]>;
  tables_deleted: string[];
};
export type GdprErasureJobCreate = {
  customer_user_ids: number[];
  selector?: ErasureSelectorIn | null;
  mode?: ErasureMode;
  seed?: number | null;
  /** delete mode only: also hard-delete the customer's tickets + FK children. */
  delete_tickets?: boolean;
  confirm: true;
};
export type GdprErasureJobOut = {
  id: number;
  mode: string;
  selector: string;
  resolved_logins: string;
  status: string;
  counts: string;
  seed?: number | null;
  actor: string;
  force_parallel: boolean;
  created: string;
  applied_at: string;
  rolled_back_at?: string | null;
  backup_expires_at: string;
};
export type GdprErasureJobDetailOut = GdprErasureJobOut & {
  counts_parsed: Record<string, number>;
  resolved_logins_parsed: string[];
  selector_parsed: Record<string, unknown>;
};
export type GdprJobListParams = {
  page?: number;
  pageSize?: number;
  status?: "applied" | "rolled_back" | "purged" | null;
  mode?: ErasureMode | null;
  q?: string | null;
  from?: string | null;
  to?: string | null;
};
export type GdprRollbackOut = { restored_rows: number };
export type GdprPurgeOut = { deleted_backups: number };
export type GdprSelectorCountRequest = { selector: ErasureSelectorIn };
export type GdprSelectorCountOut = { count: number };
export type GdprCustomerRecordPreviewRequest = {
  login: string;
  mode?: ErasureMode;
  delete_tickets?: boolean;
  seed?: number | null;
};
export type GdprFieldPreviewOut = {
  field: string;
  before?: unknown;
  after?: unknown;
  changed: boolean;
  occurrences?: number | null;
};
export type GdprDeleteSummaryRowOut = { table: string; count: number };
export type GdprCustomerRecordPreviewOut = {
  login: string;
  mode: ErasureMode;
  fields: GdprFieldPreviewOut[];
  delete_summary: GdprDeleteSummaryRowOut[];
};
export type PostmasterFilterOut = Schemas["PostmasterFilterOut"];
export type PostmasterFilterRuleOut = Schemas["PostmasterFilterRuleOut"];
export type PostmasterFilterWrite = Schemas["PostmasterFilterWrite"];
export type PostmasterMatchRuleIn = Schemas["PostmasterMatchRuleIn"];
export type PostmasterSetRuleIn = Schemas["PostmasterSetRuleIn"];
export type AclOut = Schemas["AclOut"];
// Hand-written until openapi.json is regenerated for ACL write schemas.
export type AclCreate = {
  name: string;
  comments?: string | null;
  description?: string | null;
  valid_id?: number;
  stop_after_match?: number | null;
  config_match?: string | null;
  config_change?: string | null;
};
export type AclUpdate = {
  name?: string | null;
  comments?: string | null;
  description?: string | null;
  valid_id?: number | null;
  stop_after_match?: number | null;
  config_match?: string | null;
  config_change?: string | null;
};
export type GenericAgentJobOut = Schemas["GenericAgentJobOut"];
export type SystemAddressOut = Schemas["SystemAddressOut"];
export type FollowUpPossibleOut = Schemas["FollowUpPossibleOut"];

// ── Stats ────────────────────────────────────────────────────────────────
// Hand-written (not generated from schema.d.ts): openapi.json/schema.d.ts
// are not currently kept in sync with every backend subsystem in this
// monorepo, so these mirror tiqora/stats/schemas.py directly rather than
// forcing a full openapi.json regeneration (which would otherwise pull in
// every other in-flight backend feature's routes as an unrelated diff).
export type VolumePointOut = { bucket: string; created: number; closed: number };
export type TicketVolumeOut = { granularity: string; points: VolumePointOut[] };
export type DimensionCountOut = { id: number | null; label: string; count: number };
export type OpenSnapshotOut = { dimension: string; items: DimensionCountOut[] };
export type SlaStatsOut = {
  total: number;
  escalated: number;
  first_response_breached: number;
  update_breached: number;
  solution_breached: number;
  first_response_minutes: number[];
  solution_minutes: number[];
};
export type AgentWorkloadItemOut = {
  user_id: number;
  login: string;
  name: string;
  owned_open: number;
  closed_in_period: number;
};
export type BacklogPointOut = { bucket: string; open_count: number };
export type BacklogTrendOut = { granularity: string; points: BacklogPointOut[] };
export type StatsGranularity = "day" | "week" | "month";
export type StatsDimension = "queue" | "state" | "priority" | "owner";

export type StatsFilterParams = {
  date_from?: string;
  date_to?: string;
  queue_id?: number;
  state_id?: number;
  priority_id?: number;
  type_id?: number;
  customer_id?: string;
};

// ── Calendar ─────────────────────────────────────────────────────────────
// Hand-written (see the Stats block above for rationale): mirrors
// tiqora/calendar/schemas.py directly rather than requiring an openapi.json
// regeneration.
export type CalendarOut = {
  id: number;
  group_id: number;
  name: string;
  color: string;
  valid: boolean;
};

export type RecurrenceIn = {
  type: "Daily" | "Weekly" | "Monthly" | "Yearly";
  interval?: number;
  count?: number | null;
  until?: string | null;
};

export type AppointmentIn = {
  calendar_id: number;
  title: string;
  description?: string | null;
  location?: string | null;
  start_time: string;
  end_time: string;
  all_day?: boolean;
  team_id?: string | null;
  resource_id?: string | null;
  recurrence?: RecurrenceIn | null;
};

export type AppointmentUpdateIn = {
  title?: string | null;
  description?: string | null;
  location?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  all_day?: boolean | null;
  team_id?: string | null;
  resource_id?: string | null;
  recurrence?: RecurrenceIn | null;
  clear_recurrence?: boolean;
};

export type AppointmentOut = {
  id: number;
  parent_id: number | null;
  calendar_id: number;
  unique_id: string;
  title: string;
  description: string | null;
  location: string | null;
  start_time: string;
  end_time: string;
  all_day: boolean;
  team_id: string | null;
  resource_id: string | null;
  recurring: boolean;
  recur_type: string | null;
  recur_interval: number | null;
  recur_count: number | null;
  recur_until: string | null;
  create_time: string | null;
  change_time: string | null;
};

export type OccurrenceOut = {
  appointment_id: number;
  calendar_id: number;
  title: string;
  description: string | null;
  location: string | null;
  start_time: string;
  end_time: string;
  all_day: boolean;
  is_recurring: boolean;
};

export type TicketLinkOut = {
  appointment_id: number;
  calendar_id: number;
  ticket_id: number;
  rule_id: string;
};

// ── ProcessManagement (BPM) ─────────────────────────────────────────────
// Hand-written (see the Stats block above for rationale): mirrors
// tiqora/process/schemas.py directly rather than requiring an openapi.json
// regeneration.
export type ProcessSummaryOut = {
  id: number;
  entity_id: string;
  name: string;
  state_entity_id: string;
};

export type ActivityDialogSummaryOut = {
  entity_id: string;
  name: string;
  description_short: string;
};

export type ActivityDialogRefOut = {
  entity_id: string;
  name: string;
};

export type ProcessActivityOut = {
  entity_id: string;
  name: string;
  activity_dialogs: ActivityDialogRefOut[];
};

export type ProcessDetailOut = {
  id: number;
  entity_id: string;
  name: string;
  state_entity_id: string;
  start_activity_entity_id: string | null;
  activities: ProcessActivityOut[];
};

export type TicketProcessStateOut = {
  process_entity_id: string | null;
  process_name: string | null;
  activity_entity_id: string | null;
  activity_name: string | null;
  available_dialogs: ActivityDialogSummaryOut[];
  available_transitions_count: number;
};

export type ActivityDialogFieldOut = {
  display: string;
  default_value: unknown;
  description_short: string;
  description_long: string;
  config: Record<string, unknown>;
};

export type ActivityDialogDetailOut = {
  entity_id: string;
  name: string;
  description_short: string;
  description_long: string;
  field_order: string[];
  fields: Record<string, ActivityDialogFieldOut>;
  submit_advice_text: string;
  submit_button_text: string;
};

export type ProcessStartIn = {
  process_entity_id: string;
};

export type ActivityDialogSubmitIn = {
  activity_dialog_entity_id: string;
  field_values: Record<string, unknown>;
};

export type ActivityDialogSubmitOut = {
  activity_changed: boolean;
  new_activity_entity_id: string | null;
  transition_entity_id: string | null;
  unsupported_actions: string[];
  state: TicketProcessStateOut;
};

// ── Reference (agent pickers) ────────────────────────────────────────────
// Hand-written (see the Stats block above for rationale): mirrors
// tiqora/api/v1/reference.py directly rather than requiring an openapi.json
// regeneration (which would pull in every other in-flight backend route as an
// unrelated diff).
export type PriorityRef = { id: number; name: string };
export type StateRef = { id: number; name: string; type_name: string };
export type AgentRef = { id: number; login: string; full_name: string };
export type CustomerRef = {
  login: string;
  email: string;
  customer_id: string;
  full_name: string;
};
export type QueueRef = { id: number; name: string };

/** GET /api/v1/reference/compose-context — new-ticket compose preview per queue. */
export type ComposeContext = {
  from_address: string;
  signature: string;
  signature_is_html: boolean;
  rich_text: boolean;
};

/** Compact ticket hit for agent link/merge pickers (GET /api/v1/tickets/search). */
export type TicketSearchHit = {
  ticket_id: number;
  tn: string;
  title: string;
  queue?: string | null;
  state?: string | null;
  state_type?: string | null;
};

/** Agent-side customer-user create body (POST /api/v1/customers). */
export type AgentCustomerCreateInput = {
  login: string;
  email: string;
  first_name: string;
  last_name: string;
  customer_id: string;
  phone?: string | null;
};

export type AgentCustomerCreateOut = {
  login: string;
  email: string;
  customer_id: string;
  first_name: string;
  last_name: string;
};

// Hand-written to match tiqora/api/v1/tickets.py's TicketCreateRequest, which
// the generated schema for this route does not yet reflect (agent create needs
// queue/state/priority/owner, not the portal-style title/body). The initial
// message is added as a separate article after creation. See the Stats/Ref
// blocks above for why we hand-write instead of regenerating openapi.json.
export type AgentTicketCreateInput = {
  title: string;
  queue_id: number;
  state_id: number;
  priority_id: number;
  owner_id: number;
  customer_user_id?: string | null;
};

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly path: string;

  constructor(status: number, detail: unknown, path: string) {
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "detail" in detail
          ? String((detail as { detail: unknown }).detail)
          : `HTTP ${status}`;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.path = path;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

export type ApiClientOptions = {
  /** API origin, e.g. "" for same-origin / Vite proxy, or "http://localhost:8000" */
  baseUrl?: string;
  /** Called on 401 before the error is thrown (except for login itself). */
  onUnauthorized?: () => void;
  /** Paths that must not trigger onUnauthorized (default: login). */
  skipAuthRedirectPaths?: string[];
  fetch?: typeof fetch;
};

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

function joinUrl(base: string, path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  const b = base.replace(/\/$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${b}${p}`;
}

export class ApiClient {
  readonly baseUrl: string;
  private readonly onUnauthorized?: () => void;
  private readonly skipAuthRedirectPaths: string[];
  private readonly fetchImpl: typeof fetch;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? "";
    this.onUnauthorized = options.onUnauthorized;
    this.skipAuthRedirectPaths = options.skipAuthRedirectPaths ?? [
      "/api/v1/auth/login",
      "/api/v1/auth/me",
      // Pending-2FA / forced-enroll ceremonies must not bounce to /login on 401.
      "/api/v1/auth/passkey/authenticate",
      "/api/v1/auth/passkey/register",
      "/api/v1/auth/totp/verify",
      "/api/v1/auth/totp/enroll",
      "/api/v1/auth/totp/confirm",
      "/api/portal/auth/login",
      "/api/portal/auth/me",
    ];
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  async request<T>(
    method: HttpMethod,
    path: string,
    init?: {
      body?: unknown;
      /** Scalar values use ``set``; arrays emit repeated keys (FastAPI list Query). */
      query?: Record<
        string,
        | string
        | number
        | boolean
        | null
        | undefined
        | ReadonlyArray<string | number | boolean>
      >;
      headers?: Record<string, string>;
      signal?: AbortSignal;
    },
  ): Promise<T> {
    let url = joinUrl(this.baseUrl, path);
    if (init?.query) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(init.query)) {
        if (v === undefined || v === null || v === "") continue;
        if (Array.isArray(v)) {
          for (const item of v) {
            if (item === undefined || item === null || item === "") continue;
            qs.append(k, String(item));
          }
          continue;
        }
        qs.set(k, String(v));
      }
      const s = qs.toString();
      if (s) url += (url.includes("?") ? "&" : "?") + s;
    }

    const headers: Record<string, string> = {
      Accept: "application/json",
      ...init?.headers,
    };
    let body: BodyInit | undefined;
    if (init?.body instanceof FormData) {
      body = init.body;
    } else if (init?.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(init.body);
    }

    const res = await this.fetchImpl(url, {
      method,
      headers,
      body,
      credentials: "include",
      signal: init?.signal,
    });

    if (res.status === 204) {
      return undefined as T;
    }

    const contentType = res.headers.get("content-type") ?? "";
    const isJson = contentType.includes("application/json");
    const payload = isJson
      ? await res.json().catch(() => null)
      : await res.text().catch(() => null);

    if (!res.ok) {
      if (
        res.status === 401 &&
        this.onUnauthorized &&
        !this.skipAuthRedirectPaths.some((p) => path.startsWith(p))
      ) {
        this.onUnauthorized();
      }
      throw new ApiError(res.status, payload ?? res.statusText, path);
    }

    return payload as T;
  }

  // ── Auth ──────────────────────────────────────────────────────────────

  login(body: LoginRequest, signal?: AbortSignal) {
    return this.request<LoginResponse>("POST", "/api/v1/auth/login", {
      body,
      signal,
    });
  }

  me(signal?: AbortSignal) {
    return this.request<UserMe>("GET", "/api/v1/auth/me", { signal });
  }

  /** Persist Znuny-compatible UserLanguage (also drives notification templates). */
  setMyLanguage(language: string, signal?: AbortSignal) {
    return this.request<UserMe>("PUT", "/api/v1/auth/me/language", {
      body: { language },
      signal,
    });
  }

  logout(signal?: AbortSignal) {
    return this.request<void>("POST", "/api/v1/auth/logout", { signal });
  }

  authMethods(signal?: AbortSignal) {
    return this.request<AuthMethodsOut>("GET", "/api/v1/auth/methods", { signal });
  }

  totpVerify(body: TOTPCodeIn, signal?: AbortSignal) {
    return this.request<LoginResponse>("POST", "/api/v1/auth/totp/verify", {
      body,
      signal,
    });
  }

  totpEnroll(signal?: AbortSignal) {
    return this.request<TOTPEnrollOut>("POST", "/api/v1/auth/totp/enroll", { signal });
  }

  totpConfirm(body: TOTPCodeIn, signal?: AbortSignal) {
    return this.request<TOTPStatusOut>("POST", "/api/v1/auth/totp/confirm", {
      body,
      signal,
    });
  }

  totpDisable(body: TOTPCodeIn, signal?: AbortSignal) {
    return this.request<TOTPStatusOut>("DELETE", "/api/v1/auth/totp", {
      body,
      signal,
    });
  }

  totpStatus(signal?: AbortSignal) {
    return this.request<TOTPStatusOut>("GET", "/api/v1/auth/totp/status", { signal });
  }

  // ── Passkeys (WebAuthn) ─────────────────────────────────────────────

  passkeyRegisterBegin(signal?: AbortSignal) {
    return this.request<PasskeyOptionsJSON>("POST", "/api/v1/auth/passkey/register/begin", {
      signal,
    });
  }

  passkeyRegisterFinish(body: PasskeyRegisterFinishIn, signal?: AbortSignal) {
    return this.request<PasskeyStatusOut>("POST", "/api/v1/auth/passkey/register/finish", {
      body,
      signal,
    });
  }

  passkeyAuthenticateBegin(signal?: AbortSignal) {
    return this.request<PasskeyOptionsJSON>(
      "POST",
      "/api/v1/auth/passkey/authenticate/begin",
      { signal },
    );
  }

  passkeyAuthenticateFinish(body: PasskeyAuthenticateFinishIn, signal?: AbortSignal) {
    return this.request<LoginResponse>("POST", "/api/v1/auth/passkey/authenticate/finish", {
      body,
      signal,
    });
  }

  passkeyList(signal?: AbortSignal) {
    return this.request<PasskeyOut[]>("GET", "/api/v1/auth/passkey", { signal });
  }

  passkeyDelete(passkeyId: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/auth/passkey/${passkeyId}`, { signal });
  }

  /** Browser-navigates to the OIDC provider; not a fetch (redirect flow). */
  oidcLoginUrl(): string {
    return "/api/v1/auth/oidc/login";
  }

  // ── Queues ────────────────────────────────────────────────────────────

  listQueues(signal?: AbortSignal) {
    return this.request<QueueNode[]>("GET", "/api/v1/queues", { signal });
  }

  // ── Tickets ───────────────────────────────────────────────────────────

  listTickets(
    params: {
      queue_id?: number;
      state_id?: number;
      state_type?: string;
      owner_id?: number;
      customer_id?: string;
      /** Filter by responsible agent user id. */
      responsible_id?: number;
      /** Filter by service id. */
      service_id?: number;
      /** True = lock/tmp_lock only; False = unlock only. */
      locked?: boolean;
      /** Tickets watched by this agent user id. */
      watcher_user_id?: number;
      /** True = any escalation_* epoch already in the past. */
      escalated?: boolean;
      offset?: number;
      limit?: number;
      sort?: string;
      order?: string;
      /** Admins only — also list archived tickets (ignored for non-admins). */
      include_archived?: boolean;
    } = {},
    signal?: AbortSignal,
  ) {
    return this.request<PaginatedTickets>("GET", "/api/v1/tickets", {
      query: params,
      signal,
    });
  }

  /** Open/new counts for tickets owned by the current agent (nav badges). */
  myTicketCounts(signal?: AbortSignal) {
    return this.request<MyTicketCounts>("GET", "/api/v1/tickets/my-counts", {
      signal,
    });
  }

  /** KPI-tile counts for the agent dashboard: owned open/new, unclaimed new, escalated. */
  dashboardSummary(signal?: AbortSignal) {
    return this.request<DashboardSummary>("GET", "/api/v1/tickets/dashboard-summary", {
      signal,
    });
  }

  /**
   * Permission-scoped ticket search for link/merge pickers.
   * Matches tn + title; only queues the agent has at least `ro` on.
   */
  searchTickets(
    params: { q: string; limit?: number },
    signal?: AbortSignal,
  ) {
    return this.request<TicketSearchHit[]>("GET", "/api/v1/tickets/search", {
      query: params,
      signal,
    });
  }

  getTicket(ticketId: number, signal?: AbortSignal) {
    return this.request<TicketDetail>(
      "GET",
      `/api/v1/tickets/${ticketId}`,
      { signal },
    );
  }

  /** Closed tickets similar to *ticketId* (Meili keyword rank v1). */
  getSimilarTickets(ticketId: number, signal?: AbortSignal) {
    return this.request<SimilarTicketsOut>(
      "GET",
      `/api/v1/tickets/${ticketId}/similar`,
      { signal },
    );
  }

  listArticles(ticketId: number, signal?: AbortSignal) {
    return this.request<ArticleListItem[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/articles`,
      { signal },
    );
  }

  getArticleBody(ticketId: number, articleId: number, signal?: AbortSignal) {
    return this.request<ArticleBody>(
      "GET",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/body`,
      { signal },
    );
  }

  listAttachments(ticketId: number, articleId: number, signal?: AbortSignal) {
    return this.request<AttachmentMetaOut[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/attachments`,
      { signal },
    );
  }

  attachmentDownloadUrl(
    ticketId: number,
    articleId: number,
    attachmentId: number,
    download = true,
  ): string {
    const q = download ? "?download=true" : "";
    return joinUrl(
      this.baseUrl,
      `/api/v1/tickets/${ticketId}/articles/${articleId}/attachments/${attachmentId}${q}`,
    );
  }

  listHistory(
    ticketId: number,
    order: "asc" | "desc" = "desc",
    signal?: AbortSignal,
  ) {
    return this.request<HistoryEntry[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/history?order=${order}`,
      { signal },
    );
  }

  createArticle(ticketId: number, body: ArticleCreateRequest, signal?: AbortSignal) {
    return this.request<ArticleCreateResponse>(
      "POST",
      `/api/v1/tickets/${ticketId}/articles`,
      { body, signal },
    );
  }

  getReplyDraft(
    ticketId: number,
    articleId: number,
    replyAll = false,
    signal?: AbortSignal,
  ) {
    return this.request<ReplyDraftOut>(
      "GET",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/reply-draft?reply_all=${replyAll}`,
      { signal },
    );
  }

  listTemplates(ticketId: number, signal?: AbortSignal) {
    return this.request<TemplateOut[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/templates`,
      { signal },
    );
  }

  patchTicket(ticketId: number, body: MutationRequest, signal?: AbortSignal) {
    return this.request<void>("PATCH", `/api/v1/tickets/${ticketId}`, {
      body,
      signal,
    });
  }

  mergeTicket(ticketId: number, body: MergeRequest, signal?: AbortSignal) {
    return this.request<void>("POST", `/api/v1/tickets/${ticketId}/merge`, {
      body,
      signal,
    });
  }

  createTicket(body: AgentTicketCreateInput, signal?: AbortSignal) {
    return this.request<TicketCreateResponse>("POST", "/api/v1/tickets", {
      body,
      signal,
    });
  }

  forwardArticle(
    ticketId: number,
    articleId: number,
    body: ForwardRequest,
    signal?: AbortSignal,
  ) {
    return this.request<ArticleCreateResponse>(
      "POST",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/forward`,
      { body, signal },
    );
  }

  bounceArticle(
    ticketId: number,
    articleId: number,
    body: BounceRequest,
    signal?: AbortSignal,
  ) {
    return this.request<ArticleCreateResponse>(
      "POST",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/bounce`,
      { body, signal },
    );
  }

  splitArticle(
    ticketId: number,
    articleId: number,
    body: SplitRequest,
    signal?: AbortSignal,
  ) {
    return this.request<TicketCreateResponse>(
      "POST",
      `/api/v1/tickets/${ticketId}/articles/${articleId}/split`,
      { body, signal },
    );
  }

  /** Hard-delete an internal note. Requires `rw`; 409 if the article isn't an
   * internal, non-customer-visible note. */
  deleteArticle(ticketId: number, articleId: number, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/tickets/${ticketId}/articles/${articleId}`,
      { signal },
    );
  }

  listTicketLinks(ticketId: number, signal?: AbortSignal) {
    return this.request<TicketLinkTargetOut[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/links`,
      { signal },
    );
  }

  createTicketLink(
    ticketId: number,
    body: TicketLinkCreateRequest,
    signal?: AbortSignal,
  ) {
    return this.request<void>("POST", `/api/v1/tickets/${ticketId}/links`, {
      body,
      signal,
    });
  }

  postPresence(ticketId: number, body: PresenceIn, signal?: AbortSignal) {
    return this.request<void>("POST", `/api/v1/tickets/${ticketId}/presence`, {
      body,
      signal,
    });
  }

  getPresence(ticketId: number, signal?: AbortSignal) {
    return this.request<PresenceEntry[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/presence`,
      { signal },
    );
  }

  /** Currently-online agents (Redis TTL presence). */
  getOnlineAgents(signal?: AbortSignal) {
    return this.request<OnlineAgentOut[]>("GET", "/api/v1/agents/online", {
      signal,
    });
  }

  /** Lightweight global presence heartbeat (idle-but-open sessions). */
  pingOnlinePresence(signal?: AbortSignal) {
    return this.request<void>("POST", "/api/v1/agents/presence/ping", {
      signal,
    });
  }

  /**
   * Build the CSV export URL for the current ticket-list filters. Consumed
   * via a plain navigation/anchor (cookie-authenticated download), not a
   * fetch call — mirrors {@link ApiClient.eventStreamUrl}.
   */
  exportTicketsCsvUrl(
    params: {
      queue_id?: number;
      state_id?: number;
      state_type?: string;
      owner_id?: number;
      customer_id?: string;
      responsible_id?: number;
      service_id?: number;
      locked?: boolean;
      watcher_user_id?: number;
      escalated?: boolean;
      sort?: string;
      order?: string;
      /** Admins only — also export archived tickets (ignored for non-admins). */
      include_archived?: boolean;
    } = {},
  ): string {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "") continue;
      qs.set(k, String(v));
    }
    const suffix = qs.toString();
    return joinUrl(
      this.baseUrl,
      `/api/v1/tickets/export.csv${suffix ? `?${suffix}` : ""}`,
    );
  }

  // ── Realtime events (SSE) ────────────────────────────────────────────────
  // Consumed via the browser EventSource API directly, not a fetch call —
  // this just builds the URL against the configured base URL/credentials.

  eventStreamUrl(): string {
    return joinUrl(this.baseUrl, "/api/v1/events/stream");
  }

  // ── Customers ─────────────────────────────────────────────────────────

  getCustomer(login: string, signal?: AbortSignal) {
    return this.request<CustomerUserOut>(
      "GET",
      `/api/v1/customers/${encodeURIComponent(login)}`,
      { signal },
    );
  }

  /**
   * Create a customer_user as any authenticated agent (ticket "Kunde anlegen").
   * No password — contact record only; portal auth is separate.
   */
  createCustomer(body: AgentCustomerCreateInput, signal?: AbortSignal) {
    return this.request<AgentCustomerCreateOut>("POST", "/api/v1/customers", {
      body,
      signal,
    });
  }

  // ── Reference (agent pickers, /api/v1/reference) ─────────────────────────

  listReferencePriorities(signal?: AbortSignal) {
    return this.request<PriorityRef[]>("GET", "/api/v1/reference/priorities", { signal });
  }

  listReferenceTypes(signal?: AbortSignal) {
    return this.request<TypeRef[]>("GET", "/api/v1/reference/types", { signal });
  }

  listReferenceServices(signal?: AbortSignal) {
    return this.request<ServiceRef[]>("GET", "/api/v1/reference/services", { signal });
  }

  listReferenceSlas(
    params: { service_id?: number } = {},
    signal?: AbortSignal,
  ) {
    return this.request<SlaRef[]>("GET", "/api/v1/reference/slas", {
      query: params,
      signal,
    });
  }

  listReferenceStates(signal?: AbortSignal) {
    return this.request<StateRef[]>("GET", "/api/v1/reference/states", { signal });
  }

  listReferenceAgents(signal?: AbortSignal) {
    return this.request<AgentRef[]>("GET", "/api/v1/reference/agents", { signal });
  }

  searchReferenceCustomers(
    params: { q?: string; limit?: number } = {},
    signal?: AbortSignal,
  ) {
    return this.request<CustomerRef[]>("GET", "/api/v1/reference/customers", {
      query: params,
      signal,
    });
  }

  /**
   * Quick company + contact search for agent typeaheads.
   * Distinct from ``searchReferenceCustomers`` (contacts only).
   */
  customerQuickSearch(
    params: { q?: string; limit?: number } = {},
    signal?: AbortSignal,
  ) {
    return this.request<CustomerSearchOut>("GET", "/api/v1/reference/customer-search", {
      query: params,
      signal,
    });
  }

  /**
   * Queues for agent pickers. Pass `movable: true` for the Verschieben
   * (move) picker — only valid queues the agent has `rw` on.
   */
  listReferenceQueues(
    params: { movable?: boolean } = {},
    signal?: AbortSignal,
  ) {
    return this.request<QueueRef[]>("GET", "/api/v1/reference/queues", {
      query: params,
      signal,
    });
  }

  /** From-address + signature preview + rich-text flag for the new-ticket compose form. */
  getComposeContext(queueId: number, signal?: AbortSignal) {
    return this.request<ComposeContext>("GET", "/api/v1/reference/compose-context", {
      query: { queue_id: queueId },
      signal,
    });
  }

  // ── Search ────────────────────────────────────────────────────────────

  search(
    params: {
      q: string;
      offset?: number;
      limit?: number;
      queue_id?: number[];
      state_type?: string[];
      owner_id?: number;
      customer_id?: string;
      /** ISO date ``YYYY-MM-DD`` (inclusive day start UTC). */
      created_from?: string;
      /** ISO date ``YYYY-MM-DD`` (inclusive day end UTC). */
      created_to?: string;
      /** Result ordering. Defaults to ``changed_desc`` (most recently touched). */
      sort?: "changed_desc" | "created_desc" | "created_asc";
      /** Admins only — also search archived tickets (ignored for non-admins). */
      include_archived?: boolean;
    },
    signal?: AbortSignal,
  ) {
    return this.request<SearchResponse>("GET", "/api/v1/search", {
      query: params,
      signal,
    });
  }

  // ── Knowledge base (agent, /api/v1/kb) ──────────────────────────────────

  listKbCategories(signal?: AbortSignal) {
    return this.request<CategoryOut[]>("GET", "/api/v1/kb/categories", { signal });
  }

  createKbCategory(body: CategoryIn, signal?: AbortSignal) {
    return this.request<CategoryOut>("POST", "/api/v1/kb/categories", { body, signal });
  }

  updateKbCategory(categoryId: number, body: CategoryUpdateIn, signal?: AbortSignal) {
    return this.request<CategoryOut>("PATCH", `/api/v1/kb/categories/${categoryId}`, {
      body,
      signal,
    });
  }

  deleteKbCategory(categoryId: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/kb/categories/${categoryId}`, { signal });
  }

  listAssignableGroups(signal?: AbortSignal) {
    return this.request<AssignableGroup[]>("GET", "/api/v1/kb/assignable-groups", { signal });
  }

  listKbArticles(
    params: { category_id?: number; state?: string; tag?: string } = {},
    signal?: AbortSignal,
  ) {
    return this.request<ArticleSummary[]>("GET", "/api/v1/kb/articles", {
      query: params,
      signal,
    });
  }

  /** All KB tag names with ACL-filtered visible-article counts. */
  listKbTags(signal?: AbortSignal) {
    return this.request<KbTagOut[]>("GET", "/api/v1/kb/tags", { signal });
  }

  /** ACL-filtered agent-knowledge bundle selected by tag(s) and/or category. */
  getKbKnowledge(
    params: {
      tags?: string;
      category_id?: number;
      state?: string;
      include_content?: boolean;
    } = {},
    signal?: AbortSignal,
  ) {
    return this.request<KnowledgeBundle>("GET", "/api/v1/kb/knowledge", {
      query: params,
      signal,
    });
  }

  createKbArticle(body: KbArticleIn, signal?: AbortSignal) {
    return this.request<KbArticleOut>("POST", "/api/v1/kb/articles", { body, signal });
  }

  getKbArticle(articleId: number, signal?: AbortSignal) {
    return this.request<KbArticleOut>("GET", `/api/v1/kb/articles/${articleId}`, { signal });
  }

  updateKbArticle(articleId: number, body: KbArticleUpdateIn, signal?: AbortSignal) {
    return this.request<KbArticleOut>("PATCH", `/api/v1/kb/articles/${articleId}`, {
      body,
      signal,
    });
  }

  deleteKbArticle(articleId: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/kb/articles/${articleId}`, { signal });
  }

  publishKbArticle(articleId: number, signal?: AbortSignal) {
    return this.request<KbArticleOut>("POST", `/api/v1/kb/articles/${articleId}/publish`, {
      signal,
    });
  }

  listKbArticleVersions(articleId: number, signal?: AbortSignal) {
    return this.request<ArticleVersionOut[]>(
      "GET",
      `/api/v1/kb/articles/${articleId}/versions`,
      { signal },
    );
  }

  // KB article attachments (multipart field name `file`).
  listKbAttachments(articleId: number, signal?: AbortSignal) {
    return this.request<KbAttachmentOut[]>(
      "GET",
      `/api/v1/kb/articles/${articleId}/attachments`,
      { signal },
    );
  }

  uploadKbAttachment(articleId: number, file: File, signal?: AbortSignal) {
    const form = new FormData();
    form.append("file", file);
    return this.request<KbAttachmentOut>(
      "POST",
      `/api/v1/kb/articles/${articleId}/attachments`,
      { body: form, signal },
    );
  }

  kbAttachmentDownloadUrl(articleId: number, attachmentId: number): string {
    return joinUrl(
      this.baseUrl,
      `/api/v1/kb/articles/${articleId}/attachments/${attachmentId}`,
    );
  }

  deleteKbAttachment(articleId: number, attachmentId: number, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/kb/articles/${articleId}/attachments/${attachmentId}`,
      { signal },
    );
  }

  searchKb(params: { q: string; limit?: number }, signal?: AbortSignal) {
    return this.request<KbSearchResponse>("GET", "/api/v1/kb/search", {
      query: params,
      signal,
    });
  }

  // ── Admin ────────────────────────────────────────────────────────────────

  private adminCrud<Out, Create, Update>(base: string) {
    return {
      list: (params?: AdminListParams, signal?: AbortSignal) =>
        this.request<AdminPage<Out>>("GET", base, {
          query: {
            page: params?.page,
            page_size: params?.pageSize,
            valid: params?.valid,
            search: params?.search,
            regex: params?.regex,
            sort: params?.sort,
            order: params?.order,
          },
          signal,
        }),
      create: (body: Create, signal?: AbortSignal) =>
        this.request<Out>("POST", base, { body, signal }),
      get: (id: number | string, signal?: AbortSignal) =>
        this.request<Out>("GET", `${base}/${id}`, { signal }),
      update: (id: number | string, body: Update, signal?: AbortSignal) =>
        this.request<Out>("PATCH", `${base}/${id}`, { body, signal }),
      deactivate: (id: number | string, signal?: AbortSignal) =>
        this.request<void>("DELETE", `${base}/${id}`, { signal }),
    };
  }

  get adminUsers() {
    return this.adminCrud<UserOut, UserCreate, UserUpdate>("/api/v1/admin/users");
  }

  assignUserGroup(userId: number, body: GroupAssignment, signal?: AbortSignal) {
    return this.request<void>("PUT", `/api/v1/admin/users/${userId}/groups`, { body, signal });
  }

  revokeUserGroup(userId: number, groupId: number, permissionKey: string, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/users/${userId}/groups/${groupId}/${permissionKey}`,
      { signal },
    );
  }

  assignUserRole(userId: number, body: RoleAssignment, signal?: AbortSignal) {
    return this.request<void>("PUT", `/api/v1/admin/users/${userId}/roles`, { body, signal });
  }

  revokeUserRole(userId: number, roleId: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/admin/users/${userId}/roles/${roleId}`, {
      signal,
    });
  }

  getUserEffectivePermissions(userId: number, signal?: AbortSignal) {
    return this.request<EffectivePermissionsOut>(
      "GET",
      `/api/v1/admin/users/${userId}/effective-permissions`,
      { signal },
    );
  }

  getUserDeletable(userId: number, signal?: AbortSignal) {
    return this.request<UserDeletableOut>("GET", `/api/v1/admin/users/${userId}/deletable`, {
      signal,
    });
  }

  /** Hard delete — 409s with the blocking tables when still referenced. */
  deleteUserPermanently(userId: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/admin/users/${userId}/permanent`, { signal });
  }

  getUserLanguage(userId: number, signal?: AbortSignal) {
    return this.request<UserLanguageOut>("GET", `/api/v1/admin/users/${userId}/language`, {
      signal,
    });
  }

  setUserLanguage(userId: number, body: UserLanguageUpdate, signal?: AbortSignal) {
    return this.request<UserLanguageOut>("PUT", `/api/v1/admin/users/${userId}/language`, {
      body,
      signal,
    });
  }

  get adminGroups() {
    return this.adminCrud<GroupOut, GroupCreate, GroupUpdate>("/api/v1/admin/groups");
  }

  listGroupUsers(groupId: number, signal?: AbortSignal) {
    return this.request<UserOut[]>("GET", `/api/v1/admin/groups/${groupId}/users`, { signal });
  }

  listGroupRoles(groupId: number, signal?: AbortSignal) {
    return this.request<RoleOut[]>("GET", `/api/v1/admin/groups/${groupId}/roles`, { signal });
  }

  listGroupCustomerUsers(groupId: number, signal?: AbortSignal) {
    return this.request<CustomerUserAdminOut[]>(
      "GET",
      `/api/v1/admin/groups/${groupId}/customer-users`,
      { signal },
    );
  }

  /** Bulk assignment counts keyed by group id (`side=users|roles`). */
  listGroupAssignmentCounts(
    side: "users" | "roles",
    signal?: AbortSignal,
  ) {
    return this.request<Record<string, number>>(
      "GET",
      "/api/v1/admin/groups/assignment-counts",
      { query: { side }, signal },
    );
  }

  get adminRoles() {
    return this.adminCrud<RoleOut, RoleCreate, RoleUpdate>("/api/v1/admin/roles");
  }

  listRoleUsers(roleId: number, signal?: AbortSignal) {
    return this.request<UserOut[]>("GET", `/api/v1/admin/roles/${roleId}/users`, { signal });
  }

  /** Bulk assignment counts keyed by role id (`side=users|groups`). */
  listRoleAssignmentCounts(
    side: "users" | "groups",
    signal?: AbortSignal,
  ) {
    return this.request<Record<string, number>>(
      "GET",
      "/api/v1/admin/roles/assignment-counts",
      { query: { side }, signal },
    );
  }

  /** Bulk assignment counts keyed by user id (`side=groups|roles`). */
  listUserAssignmentCounts(
    side: "groups" | "roles",
    signal?: AbortSignal,
  ) {
    return this.request<Record<string, number>>(
      "GET",
      "/api/v1/admin/users/assignment-counts",
      { query: { side }, signal },
    );
  }

  assignRoleGroup(roleId: number, body: GroupRoleAssignment, signal?: AbortSignal) {
    return this.request<void>("PUT", `/api/v1/admin/roles/${roleId}/groups`, { body, signal });
  }

  revokeRoleGroup(roleId: number, groupId: number, permissionKey: string, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/roles/${roleId}/groups/${groupId}/${permissionKey}`,
      { signal },
    );
  }

  get adminQueues() {
    return this.adminCrud<QueueOut, QueueCreate, QueueUpdate>("/api/v1/admin/queues");
  }

  get adminStates() {
    return this.adminCrud<StateOut, StateCreate, StateUpdate>("/api/v1/admin/states");
  }

  get adminPriorities() {
    return this.adminCrud<PriorityOut, PriorityCreate, PriorityUpdate>(
      "/api/v1/admin/priorities",
    );
  }

  get adminTypes() {
    return this.adminCrud<TicketTypeOut, TicketTypeCreate, TicketTypeUpdate>(
      "/api/v1/admin/types",
    );
  }

  get adminServices() {
    return this.adminCrud<ServiceOut, ServiceCreate, ServiceUpdate>(
      "/api/v1/admin/services",
    );
  }

  get adminSlas() {
    return this.adminCrud<SlaOut, SlaCreate, SlaUpdate>("/api/v1/admin/slas");
  }

  get adminCustomerUsers() {
    return this.adminCrud<CustomerUserAdminOut, CustomerUserAdminCreate, CustomerUserAdminUpdate>(
      "/api/v1/admin/customer-users",
    );
  }

  bulkUpdateCustomerUsers(body: CustomerUserBulkUpdate, signal?: AbortSignal) {
    return this.request<CustomerUserBulkUpdateResult>(
      "PATCH",
      "/api/v1/admin/customer-users/bulk",
      { body, signal },
    );
  }

  get adminCustomerCompanies() {
    return this.adminCrud<CustomerCompanyOut, CustomerCompanyCreate, CustomerCompanyUpdate>(
      "/api/v1/admin/customer-companies",
    );
  }

  listCustomerCompanyUsers(customerId: string, signal?: AbortSignal) {
    return this.request<CustomerUserAdminOut[]>(
      "GET",
      `/api/v1/admin/customer-companies/${encodeURIComponent(customerId)}/customer-users`,
      { signal },
    );
  }

  assignCustomerCompany(login: string, customerId: string, signal?: AbortSignal) {
    return this.request<void>(
      "PUT",
      `/api/v1/admin/customer-users/${encodeURIComponent(login)}/companies`,
      { body: { customer_id: customerId }, signal },
    );
  }

  revokeCustomerCompany(login: string, customerId: string, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/customer-users/${encodeURIComponent(login)}/companies/${encodeURIComponent(customerId)}`,
      { signal },
    );
  }

  get adminSalutations() {
    return this.adminCrud<SalutationOut, SalutationWrite, SalutationUpdate>(
      "/api/v1/admin/salutations",
    );
  }

  get adminSignatures() {
    return this.adminCrud<SignatureOut, SignatureWrite, SignatureUpdate>(
      "/api/v1/admin/signatures",
    );
  }

  get adminTemplates() {
    return this.adminCrud<StandardTemplateOut, StandardTemplateCreate, StandardTemplateUpdate>(
      "/api/v1/admin/templates",
    );
  }

  /** Per-template edit-ACL (which groups + users may edit it). Admin only. */
  getTemplateEditors(templateId: number, signal?: AbortSignal) {
    return this.request<TemplateEditorsOut>(
      "GET",
      `/api/v1/admin/templates/${templateId}/editors`,
      { signal },
    );
  }

  setTemplateEditors(templateId: number, body: TemplateEditorsUpdate, signal?: AbortSignal) {
    return this.request<TemplateEditorsOut>(
      "PUT",
      `/api/v1/admin/templates/${templateId}/editors`,
      { body, signal },
    );
  }

  /** Agent-facing template editing — only templates the caller is granted. */
  get agentTemplates() {
    const base = "/api/v1/templates";
    return {
      list: (params?: AdminListParams, signal?: AbortSignal) =>
        this.request<AdminPage<StandardTemplateOut>>("GET", base, {
          query: {
            page: params?.page,
            page_size: params?.pageSize,
            valid: params?.valid,
            search: params?.search,
            sort: params?.sort,
            order: params?.order,
          },
          signal,
        }),
      get: (id: number, signal?: AbortSignal) =>
        this.request<StandardTemplateOut>("GET", `${base}/${id}`, { signal }),
      update: (id: number, body: StandardTemplateUpdate, signal?: AbortSignal) =>
        this.request<StandardTemplateOut>("PATCH", `${base}/${id}`, { body, signal }),
    };
  }

  listQueueTemplates(queueId: number, signal?: AbortSignal) {
    return this.request<StandardTemplateOut[]>(
      "GET",
      `/api/v1/admin/queues/${queueId}/templates`,
      { signal },
    );
  }

  listTemplateQueues(templateId: number, signal?: AbortSignal) {
    return this.request<QueueOut[]>("GET", `/api/v1/admin/templates/${templateId}/queues`, {
      signal,
    });
  }

  /** Bulk assignment counts keyed by template id (`side=queues|attachments`). */
  listTemplateAssignmentCounts(
    side: "queues" | "attachments",
    signal?: AbortSignal,
  ) {
    return this.request<Record<string, number>>(
      "GET",
      "/api/v1/admin/templates/assignment-counts",
      { query: { side }, signal },
    );
  }

  /** Bulk assignment counts keyed by queue id (`side=templates|auto-responses`). */
  listQueueAssignmentCounts(
    side: "templates" | "auto-responses",
    signal?: AbortSignal,
  ) {
    return this.request<Record<string, number>>(
      "GET",
      "/api/v1/admin/queues/assignment-counts",
      { query: { side }, signal },
    );
  }

  assignQueueTemplate(queueId: number, standardTemplateId: number, signal?: AbortSignal) {
    return this.request<void>("PUT", `/api/v1/admin/queues/${queueId}/templates`, {
      body: { standard_template_id: standardTemplateId },
      signal,
    });
  }

  revokeQueueTemplate(queueId: number, standardTemplateId: number, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/queues/${queueId}/templates/${standardTemplateId}`,
      { signal },
    );
  }

  get adminAttachments() {
    return this.adminCrud<
      StandardAttachmentOut,
      StandardAttachmentCreate,
      StandardAttachmentUpdate
    >("/api/v1/admin/attachments");
  }

  listTemplateAttachments(templateId: number, signal?: AbortSignal) {
    return this.request<AttachmentRefOut[]>(
      "GET",
      `/api/v1/admin/templates/${templateId}/attachments`,
      { signal },
    );
  }

  listAttachmentTemplates(attachmentId: number, signal?: AbortSignal) {
    return this.request<StandardTemplateOut[]>(
      "GET",
      `/api/v1/admin/attachments/${attachmentId}/templates`,
      { signal },
    );
  }

  /** Bulk assignment counts keyed by attachment id (`side=templates`). */
  listAttachmentAssignmentCounts(side: "templates" = "templates", signal?: AbortSignal) {
    return this.request<Record<string, number>>(
      "GET",
      "/api/v1/admin/attachments/assignment-counts",
      { query: { side }, signal },
    );
  }

  /** Replace the full set of attachments linked to a standard template. */
  replaceTemplateAttachments(
    templateId: number,
    body: TemplateAttachmentsReplace,
    signal?: AbortSignal,
  ) {
    return this.request<void>("PUT", `/api/v1/admin/templates/${templateId}/attachments`, {
      body,
      signal,
    });
  }

  listCustomerUserGroups(login: string, signal?: AbortSignal) {
    return this.request<GroupOut[]>(
      "GET",
      `/api/v1/admin/customer-users/${encodeURIComponent(login)}/groups`,
      { signal },
    );
  }

  assignCustomerUserGroup(
    login: string,
    body: CustomerUserGroupAssignment,
    signal?: AbortSignal,
  ) {
    return this.request<void>(
      "PUT",
      `/api/v1/admin/customer-users/${encodeURIComponent(login)}/groups`,
      { body, signal },
    );
  }

  revokeCustomerUserGroup(
    login: string,
    groupId: number,
    permissionKey: string,
    signal?: AbortSignal,
  ) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/customer-users/${encodeURIComponent(login)}/groups/${groupId}/${permissionKey}`,
      { signal },
    );
  }

  get adminAutoResponses() {
    return this.adminCrud<AutoResponseOut, AutoResponseCreate, AutoResponseUpdate>(
      "/api/v1/admin/auto-responses",
    );
  }

  listQueueAutoResponses(queueId: number, signal?: AbortSignal) {
    return this.request<AutoResponseOut[]>(
      "GET",
      `/api/v1/admin/queues/${queueId}/auto-responses`,
      { signal },
    );
  }

  listAutoResponseQueues(autoResponseId: number, signal?: AbortSignal) {
    return this.request<QueueOut[]>(
      "GET",
      `/api/v1/admin/auto-responses/${autoResponseId}/queues`,
      { signal },
    );
  }

  /** Bulk assignment counts keyed by auto-response id (`side=queues`). */
  listAutoResponseAssignmentCounts(side: "queues" = "queues", signal?: AbortSignal) {
    return this.request<Record<string, number>>(
      "GET",
      "/api/v1/admin/auto-responses/assignment-counts",
      { query: { side }, signal },
    );
  }

  assignQueueAutoResponse(queueId: number, autoResponseId: number, signal?: AbortSignal) {
    return this.request<void>("PUT", `/api/v1/admin/queues/${queueId}/auto-responses`, {
      body: { auto_response_id: autoResponseId },
      signal,
    });
  }

  revokeQueueAutoResponse(queueId: number, autoResponseId: number, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/queues/${queueId}/auto-responses/${autoResponseId}`,
      { signal },
    );
  }

  get adminDynamicFields() {
    return this.adminCrud<DynamicFieldOut, DynamicFieldCreate, DynamicFieldUpdate>(
      "/api/v1/admin/dynamic-fields",
    );
  }

  get adminWebhooks() {
    return this.adminCrud<WebhookOut, WebhookCreate, WebhookUpdate>("/api/v1/admin/webhooks");
  }

  /** Znuny-compatible OAuth2 mail token configs (legacy oauth2_token_config). */
  get adminOAuth2TokenConfigs() {
    const base = "/api/v1/admin/oauth2-token-configs";
    const crud = this.adminCrud<
      OAuth2TokenConfigOut,
      OAuth2TokenConfigCreate,
      OAuth2TokenConfigUpdate
    >(base);
    return {
      ...crud,
      templates: (signal?: AbortSignal) =>
        this.request<OAuth2ProviderTemplateOut[]>("GET", `${base}/templates`, { signal }),
      authorizeUrl: (id: number | string, signal?: AbortSignal) =>
        this.request<OAuth2AuthorizeUrlOut>("GET", `${base}/${id}/authorize-url`, { signal }),
      refresh: (id: number | string, signal?: AbortSignal) =>
        this.request<OAuth2TokenConfigOut>("POST", `${base}/${id}/refresh`, { signal }),
    };
  }

  /** Incoming mail accounts (legacy mail_account). */
  get adminMailAccounts() {
    return this.adminCrud<MailAccountOut, MailAccountCreate, MailAccountUpdate>(
      "/api/v1/admin/mail-accounts",
    );
  }

  /**
   * API-key lifecycle. ``create`` returns the plaintext key once
   * (``ApiKeyCreated``); list/get/update never include it.
   */
  get adminApiKeys() {
    const base = "/api/v1/admin/api-keys";
    return {
      list: (params?: AdminListParams, signal?: AbortSignal) =>
        this.request<AdminPage<ApiKeyOut>>("GET", base, {
          query: {
            page: params?.page,
            page_size: params?.pageSize,
            valid: params?.valid,
          },
          signal,
        }),
      get: (id: number | string, signal?: AbortSignal) =>
        this.request<ApiKeyOut>("GET", `${base}/${id}`, { signal }),
      create: (body: ApiKeyCreate, signal?: AbortSignal) =>
        this.request<ApiKeyCreated>("POST", base, { body, signal }),
      update: (id: number | string, body: ApiKeyUpdate, signal?: AbortSignal) =>
        this.request<ApiKeyOut>("PATCH", `${base}/${id}`, { body, signal }),
      remove: (id: number | string, signal?: AbortSignal) =>
        this.request<void>("DELETE", `${base}/${id}`, { signal }),
    };
  }

  get adminQueueVariables() {
    return this.adminCrud<QueueVariableOut, QueueVariableCreate, QueueVariableUpdate>(
      "/api/v1/admin/queue-variables",
    );
  }

  /** Site-specific physical columns on the Znuny ``queue`` table (read-only). */
  listQueuePhysicalVariables(queueId: number, signal?: AbortSignal) {
    return this.request<PhysicalQueueVariableOut[]>(
      "GET",
      `/api/v1/admin/queues/${queueId}/physical-variables`,
      { signal },
    );
  }

  get adminCustomerFields() {
    return this.adminCrud<PlaceholderFieldOut, PlaceholderFieldCreate, PlaceholderFieldUpdate>(
      "/api/v1/admin/customer-fields",
    );
  }

  listAvailableCustomerColumns(source: "customer_user" | "customer_company", signal?: AbortSignal) {
    return this.request<string[]>("GET", "/api/v1/admin/customer-fields/available-columns", {
      query: { source },
      signal,
    });
  }

  getMailOutbound(signal?: AbortSignal) {
    return this.request<MailOutboundOut>("GET", "/api/v1/admin/mail/outbound", { signal });
  }

  putMailOutbound(body: MailOutboundUpdate, signal?: AbortSignal) {
    return this.request<MailOutboundOut>("PUT", "/api/v1/admin/mail/outbound", { body, signal });
  }

  getSubjectConfig(signal?: AbortSignal) {
    return this.request<SubjectConfigOut>("GET", "/api/v1/admin/subject-config", { signal });
  }

  putSubjectConfig(body: SubjectConfigUpdate, signal?: AbortSignal) {
    return this.request<SubjectConfigOut>("PUT", "/api/v1/admin/subject-config", {
      body,
      signal,
    });
  }

  /** "Dienste" — daemon takeover status/toggle/interval (admin). */
  getDaemons(signal?: AbortSignal) {
    return this.request<DaemonListOut>("GET", "/api/v1/admin/daemons", { signal });
  }

  putDaemon(slug: string, body: DaemonUpdate, signal?: AbortSignal) {
    return this.request<DaemonServiceOut>("PUT", `/api/v1/admin/daemons/${slug}`, {
      body,
      signal,
    });
  }

  /** "System-Info" — installation-wide status aggregate (admin). */
  getSystemInfo(signal?: AbortSignal) {
    return this.request<SystemInfoOut>("GET", "/api/v1/admin/system", { signal });
  }

  /** Per-agent SSO eligibility + 2FA enforcement (admin). */
  get adminAuthConfig() {
    const base = "/api/v1/admin/auth-config";
    return {
      list: (params?: AdminListParams, signal?: AbortSignal) =>
        this.request<AdminPage<AuthConfigAgentOut>>("GET", base, {
          query: {
            page: params?.page,
            page_size: params?.pageSize,
            valid: params?.valid,
          },
          signal,
        }),
      update: (userId: number, body: AuthConfigUpdate, signal?: AbortSignal) =>
        this.request<AuthConfigAgentOut>("PUT", `${base}/${userId}`, { body, signal }),
      reset2fa: (userId: number, signal?: AbortSignal) =>
        this.request<void>("POST", `${base}/${userId}/reset-2fa`, { signal }),
      getGlobal: (signal?: AbortSignal) =>
        this.request<AuthConfigGlobalOut>("GET", `${base}/global`, { signal }),
      putGlobal: (body: AuthConfigGlobalUpdate, signal?: AbortSignal) =>
        this.request<AuthConfigGlobalOut>("PUT", `${base}/global`, { body, signal }),
    };
  }

  /** Browser-navigates for Kerberos/SPNEGO; not a fetch (redirect flow).
   * `next` is a same-site path to return to after a successful handshake. */
  spnegoLoginUrl(next?: string): string {
    const base = "/api/v1/auth/spnego";
    return next ? `${base}?next=${encodeURIComponent(next)}` : base;
  }

  testMailOutbound(body: MailOutboundTestIn = {}, signal?: AbortSignal) {
    return this.request<MailOutboundTestOut>("POST", "/api/v1/admin/mail/outbound/test", {
      body,
      signal,
    });
  }

  listMailLog(params?: MailLogListParams, signal?: AbortSignal) {
    return this.request<AdminPage<MailLogOut>>("GET", "/api/v1/admin/mail/log", {
      query: {
        page: params?.page,
        page_size: params?.pageSize,
        direction: params?.direction ?? undefined,
        status: params?.status ?? undefined,
        q: params?.q ?? undefined,
        from: params?.from ?? undefined,
        to: params?.to ?? undefined,
      },
      signal,
    });
  }

  getMailLog(id: number, signal?: AbortSignal) {
    return this.request<MailLogOut>("GET", `/api/v1/admin/mail/log/${id}`, { signal });
  }

  // ── GDPR erasure (admin) ──────────────────────────────────────────────

  get adminGdpr() {
    const base = "/api/v1/admin/gdpr";
    return {
      preview: (body: GdprErasurePreviewRequest, signal?: AbortSignal) =>
        this.request<GdprErasurePreviewOut>("POST", `${base}/preview`, { body, signal }),
      /** Fast match count for the live selector counter (same selector shape as `preview`). */
      selectorCount: (body: GdprSelectorCountRequest, signal?: AbortSignal) =>
        this.request<GdprSelectorCountOut>("POST", `${base}/selector-count`, { body, signal }),
      /** Per-customer before/after preview (read-only). */
      recordPreview: (body: GdprCustomerRecordPreviewRequest, signal?: AbortSignal) =>
        this.request<GdprCustomerRecordPreviewOut>("POST", `${base}/record-preview`, {
          body,
          signal,
        }),
      createJob: (body: GdprErasureJobCreate, signal?: AbortSignal) =>
        this.request<GdprErasureJobDetailOut>("POST", `${base}/jobs`, { body, signal }),
      listJobs: (params?: GdprJobListParams, signal?: AbortSignal) =>
        this.request<AdminPage<GdprErasureJobOut>>("GET", `${base}/jobs`, {
          query: {
            page: params?.page,
            page_size: params?.pageSize,
            status: params?.status ?? undefined,
            mode: params?.mode ?? undefined,
            q: params?.q ?? undefined,
            from: params?.from ?? undefined,
            to: params?.to ?? undefined,
          },
          signal,
        }),
      getJob: (id: number, signal?: AbortSignal) =>
        this.request<GdprErasureJobDetailOut>("GET", `${base}/jobs/${id}`, { signal }),
      rollback: (id: number, signal?: AbortSignal) =>
        this.request<GdprRollbackOut>("POST", `${base}/jobs/${id}/rollback`, { signal }),
      purgeBackup: (id: number, signal?: AbortSignal) =>
        this.request<GdprPurgeOut>("POST", `${base}/jobs/${id}/purge-backup`, { signal }),
      /** Absolute URL for downloading a job's JSON backup export. */
      backupDownloadUrl: (id: number): string =>
        joinUrl(this.baseUrl, `${base}/jobs/${id}/backup/download`),
    };
  }

  /** @deprecated Prefer `adminGdpr.preview`. */
  previewGdprErasure(body: GdprErasurePreviewRequest, signal?: AbortSignal) {
    return this.adminGdpr.preview(body, signal);
  }

  /** @deprecated Prefer `adminGdpr.createJob`. */
  createGdprErasureJob(body: GdprErasureJobCreate, signal?: AbortSignal) {
    return this.adminGdpr.createJob(body, signal);
  }

  /** @deprecated Prefer `adminGdpr.listJobs`. */
  listGdprErasureJobs(params?: GdprJobListParams, signal?: AbortSignal) {
    return this.adminGdpr.listJobs(params, signal);
  }

  /** @deprecated Prefer `adminGdpr.getJob`. */
  getGdprErasureJob(id: number, signal?: AbortSignal) {
    return this.adminGdpr.getJob(id, signal);
  }

  /** @deprecated Prefer `adminGdpr.rollback`. */
  rollbackGdprErasureJob(id: number, signal?: AbortSignal) {
    return this.adminGdpr.rollback(id, signal);
  }

  /** @deprecated Prefer `adminGdpr.purgeBackup`. */
  purgeGdprErasureBackup(id: number, signal?: AbortSignal) {
    return this.adminGdpr.purgeBackup(id, signal);
  }

  /** @deprecated Prefer `adminGdpr.backupDownloadUrl`. */
  gdprErasureBackupDownloadUrl(id: number): string {
    return this.adminGdpr.backupDownloadUrl(id);
  }

  /** Paginated admin CRUD (valid filter, edit/reactivate). */
  get adminSystemAddresses() {
    return this.adminCrud<SystemAddressOut, SystemAddressCreate, SystemAddressUpdate>(
      "/api/v1/admin/system-addresses",
    );
  }

  /**
   * Valid system addresses for pickers (queues / auto-responses).
   * Thin wrapper over the paginated list — only ``valid_id = 1`` rows.
   */
  async listSystemAddresses(signal?: AbortSignal) {
    const page = await this.adminSystemAddresses.list(
      { valid: "valid", pageSize: 500 },
      signal,
    );
    return page.items;
  }

  createSystemAddress(body: SystemAddressCreate, signal?: AbortSignal) {
    return this.adminSystemAddresses.create(body, signal);
  }

  updateSystemAddress(id: number, body: SystemAddressUpdate, signal?: AbortSignal) {
    return this.adminSystemAddresses.update(id, body, signal);
  }

  deleteSystemAddress(id: number, signal?: AbortSignal) {
    return this.adminSystemAddresses.deactivate(id, signal);
  }

  listNotificationEvents(signal?: AbortSignal) {
    return this.request<NotificationEventOut[]>("GET", "/api/v1/admin/notification-events", {
      signal,
    });
  }

  getNotificationEvent(id: number, signal?: AbortSignal) {
    return this.request<NotificationEventOut>(
      "GET",
      `/api/v1/admin/notification-events/${id}`,
      { signal },
    );
  }

  createNotificationEvent(body: NotificationEventWrite, signal?: AbortSignal) {
    return this.request<NotificationEventOut>("POST", "/api/v1/admin/notification-events", {
      body,
      signal,
    });
  }

  updateNotificationEvent(id: number, body: NotificationEventUpdate, signal?: AbortSignal) {
    return this.request<NotificationEventOut>(
      "PATCH",
      `/api/v1/admin/notification-events/${id}`,
      { body, signal },
    );
  }

  deleteNotificationEvent(id: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/admin/notification-events/${id}`, {
      signal,
    });
  }

  upsertGenericAgentJob(body: GenericAgentJobWrite, signal?: AbortSignal) {
    return this.request<GenericAgentJobOut>("PUT", "/api/v1/admin/generic-agent-jobs", {
      body,
      signal,
    });
  }

  updateGenericAgentJob(
    jobName: string,
    body: GenericAgentJobUpdate,
    signal?: AbortSignal,
  ) {
    return this.request<GenericAgentJobOut>(
      "PATCH",
      `/api/v1/admin/generic-agent-jobs/${encodeURIComponent(jobName)}`,
      { body, signal },
    );
  }

  deleteGenericAgentJob(jobName: string, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/generic-agent-jobs/${encodeURIComponent(jobName)}`,
      { signal },
    );
  }

  listTicketMentions(ticketId: number, signal?: AbortSignal) {
    return this.request<MentionOut[]>("GET", `/api/v1/tickets/${ticketId}/mentions`, {
      signal,
    });
  }

  createTicketMention(ticketId: number, body: MentionCreate, signal?: AbortSignal) {
    return this.request<MentionOut>("POST", `/api/v1/tickets/${ticketId}/mentions`, {
      body,
      signal,
    });
  }

  deleteTicketMention(ticketId: number, mentionId: number, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/tickets/${ticketId}/mentions/${mentionId}`,
      { signal },
    );
  }

  /**
   * Cross-ticket time-accounting report (permission-scoped).
   * Filters: create_by (agent), ticket_id, created_from/to (ISO datetime).
   */
  listTimeAccountingReport(
    params: {
      create_by?: number;
      ticket_id?: number;
      created_from?: string;
      created_to?: string;
      offset?: number;
      limit?: number;
    } = {},
    signal?: AbortSignal,
  ) {
    return this.request<TimeAccountingReportOut>("GET", "/api/v1/tickets/time-accounting", {
      query: params,
      signal,
    });
  }

  /** Printable HTML for a ticket (browser print / Save as PDF). */
  ticketPrintUrl(ticketId: number, params: { include_history?: boolean } = {}): string {
    const qs = new URLSearchParams();
    if (params.include_history) qs.set("include_history", "true");
    const suffix = qs.toString();
    return joinUrl(
      this.baseUrl,
      `/api/v1/tickets/${ticketId}/print${suffix ? `?${suffix}` : ""}`,
    );
  }

  listTicketTimeAccounting(ticketId: number, signal?: AbortSignal) {
    return this.request<TimeAccountingOut[]>(
      "GET",
      `/api/v1/tickets/${ticketId}/time-accounting`,
      { signal },
    );
  }

  createTicketTimeAccounting(
    ticketId: number,
    body: TimeAccountingCreate,
    signal?: AbortSignal,
  ) {
    return this.request<TimeAccountingOut>(
      "POST",
      `/api/v1/tickets/${ticketId}/time-accounting`,
      { body, signal },
    );
  }

  deleteTicketTimeAccounting(
    ticketId: number,
    entryId: number,
    signal?: AbortSignal,
  ) {
    return this.request<void>(
      "DELETE",
      `/api/v1/tickets/${ticketId}/time-accounting/${entryId}`,
      { signal },
    );
  }

  listFollowUpPossible(signal?: AbortSignal) {
    return this.request<FollowUpPossibleOut[]>("GET", "/api/v1/admin/follow-up-possible", {
      signal,
    });
  }

  // PostMaster filters (named filter = Match rows + Set rows + f_stop).
  listPostmasterFilters(signal?: AbortSignal) {
    return this.request<PostmasterFilterOut[]>("GET", "/api/v1/admin/postmaster-filters", {
      signal,
    });
  }

  getPostmasterFilter(filterName: string, signal?: AbortSignal) {
    return this.request<PostmasterFilterOut>(
      "GET",
      `/api/v1/admin/postmaster-filters/${encodeURIComponent(filterName)}`,
      { signal },
    );
  }

  createPostmasterFilter(body: PostmasterFilterWrite, signal?: AbortSignal) {
    return this.request<PostmasterFilterOut>("POST", "/api/v1/admin/postmaster-filters", {
      body,
      signal,
    });
  }

  updatePostmasterFilter(
    filterName: string,
    body: PostmasterFilterWrite,
    signal?: AbortSignal,
  ) {
    return this.request<PostmasterFilterOut>(
      "PUT",
      `/api/v1/admin/postmaster-filters/${encodeURIComponent(filterName)}`,
      { body, signal },
    );
  }

  deletePostmasterFilter(filterName: string, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/admin/postmaster-filters/${encodeURIComponent(filterName)}`,
      { signal },
    );
  }

  listAcls(signal?: AbortSignal) {
    return this.request<AclOut[]>("GET", "/api/v1/admin/acl", { signal });
  }

  getAcl(aclId: number, signal?: AbortSignal) {
    return this.request<AclOut>("GET", `/api/v1/admin/acl/${aclId}`, { signal });
  }

  createAcl(body: AclCreate, signal?: AbortSignal) {
    return this.request<AclOut>("POST", "/api/v1/admin/acl", { body, signal });
  }

  updateAcl(aclId: number, body: AclUpdate, signal?: AbortSignal) {
    return this.request<AclOut>("PATCH", `/api/v1/admin/acl/${aclId}`, { body, signal });
  }

  deleteAcl(aclId: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/admin/acl/${aclId}`, { signal });
  }

  /** ACL + attribute-relation filtered field options for new-ticket forms. */
  ticketFieldOptions(
    params?: {
      fields?: string;
      action?: string;
      queueId?: number;
      serviceId?: number;
      typeId?: number;
      stateId?: number;
      priorityId?: number;
      slaId?: number;
    },
    signal?: AbortSignal,
  ) {
    return this.request<{
      state: Record<string, string>;
      queue: Record<string, string>;
      priority: Record<string, string>;
      type: Record<string, string>;
      service: Record<string, string>;
      sla: Record<string, string>;
    }>("GET", "/api/v1/reference/ticket-field-options", {
      query: {
        fields: params?.fields,
        action: params?.action,
        queue_id: params?.queueId,
        service_id: params?.serviceId,
        type_id: params?.typeId,
        state_id: params?.stateId,
        priority_id: params?.priorityId,
        sla_id: params?.slaId,
      },
      signal,
    });
  }

  /** ACL-filtered field options for an existing ticket. */
  ticketAclFieldOptions(
    ticketId: number,
    params?: { fields?: string; action?: string },
    signal?: AbortSignal,
  ) {
    return this.request<Record<string, Record<string, string>>>(
      "GET",
      `/api/v1/tickets/${ticketId}/field-options`,
      {
        query: { fields: params?.fields, action: params?.action },
        signal,
      },
    );
  }

  listGenericAgentJobs(signal?: AbortSignal) {
    return this.request<GenericAgentJobOut[]>("GET", "/api/v1/admin/generic-agent-jobs", {
      signal,
    });
  }

  getGenericAgentJob(jobName: string, signal?: AbortSignal) {
    return this.request<GenericAgentJobOut>(
      "GET",
      `/api/v1/admin/generic-agent-jobs/${encodeURIComponent(jobName)}`,
      { signal },
    );
  }

  // ── Customer portal (/api/portal) ────────────────────────────────────────

  portalLogin(body: LoginRequest, signal?: AbortSignal) {
    return this.request<CustomerLoginResponse>("POST", "/api/portal/auth/login", {
      body,
      signal,
    });
  }

  portalMe(signal?: AbortSignal) {
    return this.request<CustomerMe>("GET", "/api/portal/auth/me", { signal });
  }

  portalLogout(signal?: AbortSignal) {
    return this.request<void>("POST", "/api/portal/auth/logout", { signal });
  }

  portalListTickets(
    params: { state?: number; offset?: number; limit?: number } = {},
    signal?: AbortSignal,
  ) {
    return this.request<PaginatedTickets>("GET", "/api/portal/tickets", {
      query: params,
      signal,
    });
  }

  portalCreateTicket(body: PortalTicketCreateRequest, signal?: AbortSignal) {
    return this.request<PortalTicketCreateResponse>("POST", "/api/portal/tickets", {
      body,
      signal,
    });
  }

  portalGetTicket(ticketId: number, signal?: AbortSignal) {
    return this.request<TicketDetail>("GET", `/api/portal/tickets/${ticketId}`, { signal });
  }

  portalListArticles(ticketId: number, signal?: AbortSignal) {
    return this.request<ArticleListItem[]>(
      "GET",
      `/api/portal/tickets/${ticketId}/articles`,
      { signal },
    );
  }

  portalReply(ticketId: number, body: PortalReplyRequest, signal?: AbortSignal) {
    return this.request<PortalReplyResponse>("POST", `/api/portal/tickets/${ticketId}/reply`, {
      body,
      signal,
    });
  }

  portalUploadAttachment(
    ticketId: number,
    file: File,
    note = "",
    signal?: AbortSignal,
  ) {
    const form = new FormData();
    form.append("file", file);
    form.append("note", note);
    return this.request<PortalAttachmentUploadResponse>(
      "POST",
      `/api/portal/tickets/${ticketId}/attachments`,
      { body: form, signal },
    );
  }

  portalAttachmentDownloadUrl(ticketId: number, attachmentId: number): string {
    return joinUrl(
      this.baseUrl,
      `/api/portal/tickets/${ticketId}/attachments/${attachmentId}`,
    );
  }

  portalSearchKb(params: { q: string; offset?: number; limit?: number }, signal?: AbortSignal) {
    return this.request<KbSearchResponse>("GET", "/api/portal/kb/search", {
      query: params,
      signal,
    });
  }

  portalGetKbArticle(slugOrId: string, signal?: AbortSignal) {
    return this.request<PortalArticleOut>(
      "GET",
      `/api/portal/kb/articles/${encodeURIComponent(slugOrId)}`,
      { signal },
    );
  }

  // ── Stats (/api/v1/stats) ────────────────────────────────────────────────

  statsVolume(
    params: StatsFilterParams & { granularity?: StatsGranularity } = {},
    signal?: AbortSignal,
  ) {
    return this.request<TicketVolumeOut>("GET", "/api/v1/stats/volume", {
      query: params,
      signal,
    });
  }

  statsVolumeCsvUrl(params: StatsFilterParams & { granularity?: StatsGranularity } = {}): string {
    return this.buildStatsCsvUrl("/api/v1/stats/volume.csv", params);
  }

  statsOpenSnapshot(
    params: StatsFilterParams & { dimension?: StatsDimension } = {},
    signal?: AbortSignal,
  ) {
    return this.request<OpenSnapshotOut>("GET", "/api/v1/stats/open-snapshot", {
      query: params,
      signal,
    });
  }

  statsOpenSnapshotCsvUrl(params: StatsFilterParams & { dimension?: StatsDimension } = {}): string {
    return this.buildStatsCsvUrl("/api/v1/stats/open-snapshot.csv", params);
  }

  statsSla(params: StatsFilterParams = {}, signal?: AbortSignal) {
    return this.request<SlaStatsOut>("GET", "/api/v1/stats/sla", {
      query: params,
      signal,
    });
  }

  statsSlaCsvUrl(params: StatsFilterParams = {}): string {
    return this.buildStatsCsvUrl("/api/v1/stats/sla.csv", params);
  }

  statsAgentWorkload(params: StatsFilterParams = {}, signal?: AbortSignal) {
    return this.request<AgentWorkloadItemOut[]>("GET", "/api/v1/stats/agent-workload", {
      query: params,
      signal,
    });
  }

  statsAgentWorkloadCsvUrl(params: StatsFilterParams = {}): string {
    return this.buildStatsCsvUrl("/api/v1/stats/agent-workload.csv", params);
  }

  statsBacklog(
    params: StatsFilterParams & { granularity?: StatsGranularity } = {},
    signal?: AbortSignal,
  ) {
    return this.request<BacklogTrendOut>("GET", "/api/v1/stats/backlog", {
      query: params,
      signal,
    });
  }

  statsBacklogCsvUrl(params: StatsFilterParams & { granularity?: StatsGranularity } = {}): string {
    return this.buildStatsCsvUrl("/api/v1/stats/backlog.csv", params);
  }

  /**
   * Build a CSV export URL for a stats report. Consumed via a plain
   * navigation/anchor (cookie-authenticated download), not a fetch call —
   * mirrors {@link ApiClient.exportTicketsCsvUrl}.
   */
  private buildStatsCsvUrl(path: string, params: Record<string, unknown>): string {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "") continue;
      qs.set(k, String(v));
    }
    const suffix = qs.toString();
    return joinUrl(this.baseUrl, `${path}${suffix ? `?${suffix}` : ""}`);
  }

  // ── Calendar (/api/v1/calendar) ──────────────────────────────────────────

  listCalendars(signal?: AbortSignal) {
    return this.request<CalendarOut[]>("GET", "/api/v1/calendar/calendars", { signal });
  }

  listAppointments(
    params: { start: string; end: string; calendar_id?: number[] },
    signal?: AbortSignal,
  ) {
    const qs = new URLSearchParams();
    qs.set("start", params.start);
    qs.set("end", params.end);
    for (const id of params.calendar_id ?? []) qs.append("calendar_id", String(id));
    return this.request<OccurrenceOut[]>(
      "GET",
      `/api/v1/calendar/appointments?${qs.toString()}`,
      { signal },
    );
  }

  createAppointment(body: AppointmentIn, signal?: AbortSignal) {
    return this.request<AppointmentOut>("POST", "/api/v1/calendar/appointments", {
      body,
      signal,
    });
  }

  getAppointment(appointmentId: number, signal?: AbortSignal) {
    return this.request<AppointmentOut>(
      "GET",
      `/api/v1/calendar/appointments/${appointmentId}`,
      { signal },
    );
  }

  updateAppointment(appointmentId: number, body: AppointmentUpdateIn, signal?: AbortSignal) {
    return this.request<AppointmentOut>(
      "PATCH",
      `/api/v1/calendar/appointments/${appointmentId}`,
      { body, signal },
    );
  }

  deleteAppointment(
    appointmentId: number,
    params: { occurrence?: string } = {},
    signal?: AbortSignal,
  ) {
    return this.request<void>("DELETE", `/api/v1/calendar/appointments/${appointmentId}`, {
      query: params,
      signal,
    });
  }

  linkAppointmentTicket(appointmentId: number, ticketId: number, signal?: AbortSignal) {
    return this.request<TicketLinkOut>(
      "POST",
      `/api/v1/calendar/appointments/${appointmentId}/tickets/${ticketId}`,
      { signal },
    );
  }

  unlinkAppointmentTicket(appointmentId: number, ticketId: number, signal?: AbortSignal) {
    return this.request<void>(
      "DELETE",
      `/api/v1/calendar/appointments/${appointmentId}/tickets/${ticketId}`,
      { signal },
    );
  }

  listAppointmentTicketLinks(appointmentId: number, signal?: AbortSignal) {
    return this.request<TicketLinkOut[]>(
      "GET",
      `/api/v1/calendar/appointments/${appointmentId}/tickets`,
      { signal },
    );
  }

  calendarExportIcsUrl(calendarId: number): string {
    return joinUrl(this.baseUrl, `/api/v1/calendar/calendars/${calendarId}/export.ics`);
  }

  // ── ProcessManagement (BPM) (/api/v1/process) ────────────────────────────

  listProcesses(signal?: AbortSignal) {
    return this.request<ProcessSummaryOut[]>("GET", "/api/v1/process/", { signal });
  }

  getProcess(processEntityId: string, signal?: AbortSignal) {
    return this.request<ProcessDetailOut>(
      "GET",
      `/api/v1/process/${encodeURIComponent(processEntityId)}`,
      { signal },
    );
  }

  getActivityDialog(activityDialogEntityId: string, signal?: AbortSignal) {
    return this.request<ActivityDialogDetailOut>(
      "GET",
      `/api/v1/process/activity-dialog/${encodeURIComponent(activityDialogEntityId)}`,
      { signal },
    );
  }

  getTicketProcessState(ticketId: number, signal?: AbortSignal) {
    return this.request<TicketProcessStateOut>(
      "GET",
      `/api/v1/process/ticket/${ticketId}/state`,
      { signal },
    );
  }

  startTicketProcess(ticketId: number, body: ProcessStartIn, signal?: AbortSignal) {
    return this.request<TicketProcessStateOut>(
      "POST",
      `/api/v1/process/ticket/${ticketId}/start`,
      { body, signal },
    );
  }

  submitActivityDialog(ticketId: number, body: ActivityDialogSubmitIn, signal?: AbortSignal) {
    return this.request<ActivityDialogSubmitOut>(
      "POST",
      `/api/v1/process/ticket/${ticketId}/submit`,
      { body, signal },
    );
  }

  // ── Portal ProcessManagement (/api/portal/process) ─────────────────────

  portalListProcesses(signal?: AbortSignal) {
    return this.request<ProcessSummaryOut[]>("GET", "/api/portal/process/", { signal });
  }

  portalGetActivityDialog(activityDialogEntityId: string, signal?: AbortSignal) {
    return this.request<ActivityDialogDetailOut>(
      "GET",
      `/api/portal/process/activity-dialog/${encodeURIComponent(activityDialogEntityId)}`,
      { signal },
    );
  }

  portalGetTicketProcessState(ticketId: number, signal?: AbortSignal) {
    return this.request<TicketProcessStateOut>(
      "GET",
      `/api/portal/process/ticket/${ticketId}/state`,
      { signal },
    );
  }

  portalStartTicketProcess(ticketId: number, body: ProcessStartIn, signal?: AbortSignal) {
    return this.request<TicketProcessStateOut>(
      "POST",
      `/api/portal/process/ticket/${ticketId}/start`,
      { body, signal },
    );
  }

  portalSubmitActivityDialog(
    ticketId: number,
    body: ActivityDialogSubmitIn,
    signal?: AbortSignal,
  ) {
    return this.request<ActivityDialogSubmitOut>(
      "POST",
      `/api/portal/process/ticket/${ticketId}/submit`,
      { body, signal },
    );
  }

  // ── Admin Ticket Attribute Relations ───────────────────────────────────

  listTicketAttributeRelations(signal?: AbortSignal) {
    return this.request<
      Array<{
        id: number;
        filename: string;
        attribute_1: string;
        attribute_2: string;
        acl_data: string;
        priority: number;
        create_time?: string | null;
        change_time?: string | null;
      }>
    >("GET", "/api/v1/admin/ticket-attribute-relations", { signal });
  }

  getTicketAttributeRelation(relationId: number, signal?: AbortSignal) {
    return this.request<{
      id: number;
      filename: string;
      attribute_1: string;
      attribute_2: string;
      acl_data: string;
      priority: number;
    }>("GET", `/api/v1/admin/ticket-attribute-relations/${relationId}`, { signal });
  }

  createTicketAttributeRelation(
    body: { filename: string; acl_data: string; priority?: number },
    signal?: AbortSignal,
  ) {
    return this.request<{
      id: number;
      filename: string;
      attribute_1: string;
      attribute_2: string;
      acl_data: string;
      priority: number;
    }>("POST", "/api/v1/admin/ticket-attribute-relations", { body, signal });
  }

  updateTicketAttributeRelation(
    relationId: number,
    body: { filename?: string; acl_data?: string; priority?: number },
    signal?: AbortSignal,
  ) {
    return this.request<{
      id: number;
      filename: string;
      attribute_1: string;
      attribute_2: string;
      acl_data: string;
      priority: number;
    }>("PATCH", `/api/v1/admin/ticket-attribute-relations/${relationId}`, { body, signal });
  }

  deleteTicketAttributeRelation(relationId: number, signal?: AbortSignal) {
    return this.request<void>("DELETE", `/api/v1/admin/ticket-attribute-relations/${relationId}`, {
      signal,
    });
  }
}

export type { paths };
