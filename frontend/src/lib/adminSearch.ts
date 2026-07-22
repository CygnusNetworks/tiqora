/**
 * Single source of truth for the admin area's structure: every admin page,
 * which of the 5 domain groups it belongs to, and the keywords the ⌘K
 * command palette matches it on. AdminShell (sidebar), AdminHomePage
 * (dashboard quick-access) and AdminCommandPalette all derive their listing
 * from this registry so the three stay in sync automatically.
 */

export type AdminPageGroup = "access" | "tickets" | "communication" | "automation" | "system";

export type AdminPageEntry = {
  /** Also used as the React key and in `admin-nav-<slug>` / `admin-search-result-<slug>` testids. */
  slug: string;
  route: string;
  group: AdminPageGroup;
  nameKey: string;
  descriptionKey: string;
  /** German + English synonyms and setting-related terms the palette should also match. */
  keywords: string[];
};

export const ADMIN_PAGE_GROUPS: AdminPageGroup[] = [
  "access",
  "tickets",
  "communication",
  "automation",
  "system",
];

export const ADMIN_PAGES: AdminPageEntry[] = [
  // ── Zugriff & Benutzer ────────────────────────────────────────────────
  {
    slug: "users",
    route: "/admin/users",
    group: "access",
    nameKey: "admin.nav.users",
    descriptionKey: "admin.pageDescriptions.users",
    keywords: ["agent", "mitarbeiter", "benutzer", "account", "login", "employee"],
  },
  {
    slug: "groups",
    route: "/admin/groups",
    group: "access",
    nameKey: "admin.nav.groups",
    descriptionKey: "admin.pageDescriptions.groups",
    keywords: ["berechtigung", "permissions", "gruppen", "access"],
  },
  {
    slug: "roles",
    route: "/admin/roles",
    group: "access",
    nameKey: "admin.nav.roles",
    descriptionKey: "admin.pageDescriptions.roles",
    keywords: ["rollen", "rbac", "berechtigung", "permissions"],
  },
  {
    slug: "agent-roles",
    route: "/admin/agent-roles",
    group: "access",
    nameKey: "admin.nav.agentRoles",
    descriptionKey: "admin.pageDescriptions.agentRoles",
    keywords: ["zuordnung", "assignment", "rollenzuordnung", "agent roles"],
  },
  {
    slug: "agent-groups",
    route: "/admin/agent-groups",
    group: "access",
    nameKey: "admin.nav.agentGroups",
    descriptionKey: "admin.pageDescriptions.agentGroups",
    keywords: ["zuordnung", "assignment", "gruppenzuordnung", "agent groups"],
  },
  {
    slug: "role-groups",
    route: "/admin/role-groups",
    group: "access",
    nameKey: "admin.nav.roleGroups",
    descriptionKey: "admin.pageDescriptions.roleGroups",
    keywords: ["rollen", "gruppen", "zuordnung", "role groups", "assignment"],
  },
  {
    slug: "customer-users",
    route: "/admin/customer-users",
    group: "access",
    nameKey: "admin.nav.customerUsers",
    descriptionKey: "admin.pageDescriptions.customerUsers",
    keywords: ["kunde", "customer", "kundenbenutzer", "kontakt", "contact"],
  },
  {
    slug: "customer-companies",
    route: "/admin/customer-companies",
    group: "access",
    nameKey: "admin.nav.customerCompanies",
    descriptionKey: "admin.pageDescriptions.customerCompanies",
    keywords: ["kunde", "unternehmen", "firma", "company", "organisation"],
  },
  {
    slug: "customer-user-customers",
    route: "/admin/customer-user-customers",
    group: "access",
    nameKey: "admin.nav.customerUserCustomers",
    descriptionKey: "admin.pageDescriptions.customerUserCustomers",
    keywords: ["zuordnung", "unternehmenszuordnung", "assignment"],
  },
  {
    slug: "customer-user-groups",
    route: "/admin/customer-user-groups",
    group: "access",
    nameKey: "admin.nav.customerUserGroups",
    descriptionKey: "admin.pageDescriptions.customerUserGroups",
    keywords: ["zuordnung", "customer groups", "assignment"],
  },
  {
    slug: "auth-config",
    route: "/admin/auth-config",
    group: "access",
    nameKey: "admin.nav.authConfig",
    descriptionKey: "admin.pageDescriptions.authConfig",
    keywords: [
      "2fa",
      "totp",
      "passkey",
      "webauthn",
      "sso",
      "kerberos",
      "ldap",
      "authentifizierung",
      "anmeldung",
    ],
  },

  // ── Tickets & Workflow ──────────────────────────────────────────────
  {
    slug: "queues",
    route: "/admin/queues",
    group: "tickets",
    nameKey: "admin.nav.queues",
    descriptionKey: "admin.pageDescriptions.queues",
    keywords: ["postfach", "queue", "mailbox"],
  },
  {
    slug: "queue-templates",
    route: "/admin/queue-templates",
    group: "tickets",
    nameKey: "admin.nav.queueTemplates",
    descriptionKey: "admin.pageDescriptions.queueTemplates",
    keywords: ["vorlagen", "zuordnung", "templates", "queue templates"],
  },
  {
    slug: "queue-auto-responses",
    route: "/admin/queue-auto-responses",
    group: "tickets",
    nameKey: "admin.nav.queueAutoResponses",
    descriptionKey: "admin.pageDescriptions.queueAutoResponses",
    keywords: ["autoantwort", "zuordnung", "auto response", "queue"],
  },
  {
    slug: "queue-variables",
    route: "/admin/queue-variables",
    group: "tickets",
    nameKey: "admin.nav.queueVariables",
    descriptionKey: "admin.pageDescriptions.queueVariables",
    keywords: ["platzhalter", "variablen", "placeholder", "smart tags"],
  },
  {
    slug: "states",
    route: "/admin/states",
    group: "tickets",
    nameKey: "admin.nav.states",
    descriptionKey: "admin.pageDescriptions.states",
    keywords: ["status", "zustand", "ticket status"],
  },
  {
    slug: "priorities",
    route: "/admin/priorities",
    group: "tickets",
    nameKey: "admin.nav.priorities",
    descriptionKey: "admin.pageDescriptions.priorities",
    keywords: ["priorität", "priority", "dringlichkeit"],
  },
  {
    slug: "dynamic-fields",
    route: "/admin/dynamic-fields",
    group: "tickets",
    nameKey: "admin.nav.dynamicFields",
    descriptionKey: "admin.pageDescriptions.dynamicFields",
    keywords: ["custom fields", "freie felder", "metadaten"],
  },
  {
    slug: "acl",
    route: "/admin/acl",
    group: "tickets",
    nameKey: "admin.nav.acl",
    descriptionKey: "admin.pageDescriptions.acl",
    keywords: ["access control", "zugriffskontrolle", "berechtigung", "permissions"],
  },
  {
    slug: "processes",
    route: "/admin/processes",
    group: "tickets",
    nameKey: "admin.nav.processes",
    descriptionKey: "admin.pageDescriptions.processes",
    keywords: ["workflow", "prozess", "process management", "bpmn"],
  },

  // ── Kommunikation & Vorlagen ───────────────────────────────────────
  {
    slug: "templates",
    route: "/admin/templates",
    group: "communication",
    nameKey: "admin.nav.templates",
    descriptionKey: "admin.pageDescriptions.templates",
    keywords: ["vorlagen", "antwortbausteine", "canned response", "template"],
  },
  {
    slug: "template-attachments",
    route: "/admin/template-attachments",
    group: "communication",
    nameKey: "admin.nav.templateAttachments",
    descriptionKey: "admin.pageDescriptions.templateAttachments",
    keywords: ["anhänge", "zuordnung", "template attachments"],
  },
  {
    slug: "attachments",
    route: "/admin/attachments",
    group: "communication",
    nameKey: "admin.nav.attachments",
    descriptionKey: "admin.pageDescriptions.attachments",
    keywords: ["dateien", "anhänge", "upload", "files"],
  },
  {
    slug: "signatures",
    route: "/admin/signatures",
    group: "communication",
    nameKey: "admin.nav.signatures",
    descriptionKey: "admin.pageDescriptions.signatures",
    keywords: ["signatur", "unterschrift", "email signature"],
  },
  {
    slug: "salutations",
    route: "/admin/salutations",
    group: "communication",
    nameKey: "admin.nav.salutations",
    descriptionKey: "admin.pageDescriptions.salutations",
    keywords: ["anrede", "grußformel", "greeting"],
  },
  {
    slug: "auto-responses",
    route: "/admin/auto-responses",
    group: "communication",
    nameKey: "admin.nav.autoResponses",
    descriptionKey: "admin.pageDescriptions.autoResponses",
    keywords: ["autoantwort", "abwesenheit", "auto reply", "vacation"],
  },
  {
    slug: "mail-outbound",
    route: "/admin/mail-outbound",
    group: "communication",
    nameKey: "admin.nav.mailOutbound",
    descriptionKey: "admin.pageDescriptions.mailOutbound",
    keywords: ["smtp", "mail", "versand", "absender", "outbound", "email"],
  },
  {
    slug: "subject-config",
    route: "/admin/subject-config",
    group: "communication",
    nameKey: "admin.nav.subjectConfig",
    descriptionKey: "admin.pageDescriptions.subjectConfig",
    keywords: ["betreff", "hook", "subject", "referenznummer", "ticket number"],
  },
  {
    slug: "postmaster-filters",
    route: "/admin/postmaster-filters",
    group: "communication",
    nameKey: "admin.nav.postmasterFilters",
    descriptionKey: "admin.pageDescriptions.postmasterFilters",
    keywords: ["filter", "eingang", "email rules", "x-header"],
  },

  // ── Automatisierung ──────────────────────────────────────────────────
  {
    slug: "generic-agent-jobs",
    route: "/admin/generic-agent-jobs",
    group: "automation",
    nameKey: "admin.nav.genericAgentJobs",
    descriptionKey: "admin.pageDescriptions.genericAgentJobs",
    keywords: ["automatisierung", "jobs", "cron", "genericagent", "scheduled tasks"],
  },
  {
    slug: "webhooks",
    route: "/admin/webhooks",
    group: "automation",
    nameKey: "admin.nav.webhooks",
    descriptionKey: "admin.pageDescriptions.webhooks",
    keywords: ["integration", "callback", "http hook"],
  },
  {
    slug: "api-keys",
    route: "/admin/api-keys",
    group: "automation",
    nameKey: "admin.nav.apiKeys",
    descriptionKey: "admin.pageDescriptions.apiKeys",
    keywords: ["api", "token", "schlüssel", "zugriffsschlüssel", "integration"],
  },
  {
    slug: "ai",
    route: "/admin/ai",
    group: "automation",
    nameKey: "admin.nav.ai",
    descriptionKey: "admin.pageDescriptions.ai",
    keywords: ["ki", "ai", "llm", "assistent", "autoreply", "einstellungen"],
  },
  {
    slug: "ai-providers",
    route: "/admin/ai/providers",
    group: "automation",
    nameKey: "admin.nav.aiProviders",
    descriptionKey: "admin.pageDescriptions.aiProviders",
    keywords: ["ki", "llm", "provider", "openai", "anthropic", "modell"],
  },
  {
    slug: "ai-mcp",
    route: "/admin/ai/mcp",
    group: "automation",
    nameKey: "admin.nav.aiMcp",
    descriptionKey: "admin.pageDescriptions.aiMcp",
    keywords: ["mcp", "tools", "werkzeuge", "model context protocol"],
  },
  {
    slug: "ai-queues",
    route: "/admin/ai/queues",
    group: "automation",
    nameKey: "admin.nav.aiQueues",
    descriptionKey: "admin.pageDescriptions.aiQueues",
    keywords: ["ki", "queue", "autonomie", "eskalation", "policy"],
  },

  // ── System & Betrieb ─────────────────────────────────────────────────
  {
    slug: "daemons",
    route: "/admin/daemons",
    group: "system",
    nameKey: "admin.nav.daemons",
    descriptionKey: "admin.pageDescriptions.daemons",
    keywords: [
      "daemon",
      "cron",
      "postmaster",
      "eskalation",
      "imap",
      "hintergrund",
      "worker",
      "dienste",
      "services",
    ],
  },
  {
    slug: "gdpr",
    route: "/admin/gdpr",
    group: "system",
    nameKey: "admin.nav.gdpr",
    descriptionKey: "admin.pageDescriptions.gdpr",
    keywords: ["gdpr", "dsgvo", "löschen", "auskunft", "anonymisierung", "datenschutz", "privacy"],
  },
  {
    slug: "mail-log",
    route: "/admin/mail-log",
    group: "system",
    nameKey: "admin.nav.mailLog",
    descriptionKey: "admin.pageDescriptions.mailLog",
    keywords: ["kommunikationsprotokoll", "mail log", "versandprotokoll", "zustellung"],
  },
  {
    slug: "customer-fields",
    route: "/admin/customer-fields",
    group: "system",
    nameKey: "admin.nav.customerFields",
    descriptionKey: "admin.pageDescriptions.customerFields",
    keywords: ["platzhalter", "variablen", "customer felder", "placeholder"],
  },
];

export function adminPagesByGroup(group: AdminPageGroup): AdminPageEntry[] {
  return ADMIN_PAGES.filter((page) => page.group === group);
}

/**
 * Ranks admin pages for the ⌘K palette: a name hit outranks a keyword hit,
 * which outranks a description hit. All matches are case-insensitive
 * substrings. `t` resolves the i18n keys so ranking follows the active
 * locale. An empty query returns the pages in registry order.
 */
export function rankAdminPages(
  pages: AdminPageEntry[],
  query: string,
  t: (key: string) => string,
): AdminPageEntry[] {
  const term = query.trim().toLowerCase();
  if (!term) return pages;

  const scored: { page: AdminPageEntry; score: number }[] = [];
  for (const page of pages) {
    const name = t(page.nameKey).toLowerCase();
    const description = t(page.descriptionKey).toLowerCase();
    let score = 0;
    if (name.includes(term)) {
      score = 3;
    } else if (page.keywords.some((keyword) => keyword.toLowerCase().includes(term))) {
      score = 2;
    } else if (description.includes(term)) {
      score = 1;
    }
    if (score > 0) scored.push({ page, score });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.map((s) => s.page);
}
