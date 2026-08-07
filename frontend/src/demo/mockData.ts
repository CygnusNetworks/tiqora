/**
 * Rich, representative mock dataset shared by the README screenshot fixture
 * (e2e/fixtures/rich-mock.ts) and the MSW-backed GitHub Pages demo
 * (src/demo/handlers.ts). English data; admin resource lists return the
 * `AdminPage` envelope, agent endpoints match the real API shapes.
 *
 * `resolveData(pathname, method)` is pure (no auth state) — each consumer layers
 * its own auth handling (the fixture gates behind a login flow; the demo is
 * auto-authenticated).
 */

function page<T>(items: T[]) {
  return { items, total: items.length, page: 1, page_size: 500 };
}

const t0 = "2026-06-01T00:00:00Z";

export const demoUser = {
  id: 1,
  login: "aturner",
  first_name: "Alex",
  last_name: "Turner",
  auth_method: "password",
  is_admin: true,
  email: "alex.turner@example.com",
};

// ── Queues (agent tree with counts) ─────────────────────────────────────────
const agentQueues = [
  { id: 1, name: "Support", group_id: 2, valid: true, counts: { open: 14, locked: 3, unlocked: 11, total: 22 },
    children: [
      { id: 2, name: "Support::Level 1", group_id: 2, parent_name: "Support", valid: true, counts: { open: 9, locked: 2, unlocked: 7, total: 13 }, children: [] },
      { id: 3, name: "Support::Level 2", group_id: 2, parent_name: "Support", valid: true, counts: { open: 5, locked: 1, unlocked: 4, total: 6 }, children: [] },
    ] },
  { id: 4, name: "Incidents", group_id: 3, valid: true, counts: { open: 7, locked: 1, unlocked: 6, total: 12 }, children: [] },
  { id: 5, name: "Sales", group_id: 4, valid: true, counts: { open: 4, locked: 0, unlocked: 4, total: 9 }, children: [] },
  { id: 6, name: "Billing", group_id: 5, valid: true, counts: { open: 3, locked: 0, unlocked: 3, total: 8 }, children: [] },
];

// ── Tickets ─────────────────────────────────────────────────────────────────
const CUSTOMERS = [
  { cid: "ACME", login: "j.doe@acme.example", name: "Jane Doe" },
  { cid: "ACME", login: "m.reed@acme.example", name: "Marcus Reed" },
  { cid: "NORTHWIND", login: "s.patel@northwind.example", name: "Sara Patel" },
  { cid: "NORTHWIND", login: "l.gomez@northwind.example", name: "Luis Gomez" },
  { cid: "GLOBEX", login: "k.wu@globex.example", name: "Karen Wu" },
  { cid: "INITECH", login: "t.hall@initech.example", name: "Tom Hall" },
];
const SUBJECTS = [
  "Printer offline in building A", "VPN access request", "Cannot log into portal",
  "Invoice discrepancy for March", "New laptop provisioning", "Email delivery delayed",
  "Password reset for shared mailbox", "Website contact form broken", "Slow database queries",
  "Request: additional license seats", "Two-factor app not accepting codes", "Onboarding new starter",
  "Firewall rule change request", "Backup job failed overnight",
];
const STATES = [
  { state_id: 1, state: "new", state_type: "open" },
  { state_id: 4, state: "open", state_type: "open" },
  { state_id: 6, state: "pending reminder", state_type: "pending reminder" },
  { state_id: 2, state: "closed successful", state_type: "closed" },
];
const PRIOS = [
  { priority_id: 2, priority: "2 low" },
  { priority_id: 3, priority: "3 normal" },
  { priority_id: 4, priority: "4 high" },
  { priority_id: 5, priority: "5 very high" },
];
const QROUTE = [
  { queue_id: 2, queue_name: "Support::Level 1" },
  { queue_id: 3, queue_name: "Support::Level 2" },
  { queue_id: 4, queue_name: "Incidents" },
  { queue_id: 5, queue_name: "Sales" },
  { queue_id: 6, queue_name: "Billing" },
];
const OWNERS = [
  { owner_id: 1, owner_login: "aturner", owner_name: "Alex Turner" },
  { owner_id: 2, owner_login: "bshah", owner_name: "Bianca Shah" },
  { owner_id: 3, owner_login: "cmorris", owner_name: "Chris Morris" },
];

// Tickets flagged for the queue-row icons (paperclip / ✦ AI summary). Kept to
// roughly a third of the 14 tickets so the icons read as a natural subset
// rather than decorating every row.
const ATTACHMENT_TICKET_IDS = new Set([100, 101, 103, 111, 113]);
const AI_SUMMARY_TICKET_IDS = new Set([100, 105, 108]);

const ticketItems = SUBJECTS.map((title, i) => {
  const id = 100 + i;
  const c = CUSTOMERS[i % CUSTOMERS.length];
  const s = STATES[i % STATES.length];
  const p = PRIOS[(i * 3) % PRIOS.length];
  const q = QROUTE[i % QROUTE.length];
  const o = OWNERS[i % OWNERS.length];
  const day = 10 + (i % 12);
  return {
    id, tn: `2026070${String(1000 + i)}`, title,
    ...q, ...s, ...p, lock_id: 1, lock: "unlock", ...o,
    customer_id: c.cid, customer_user_id: c.login,
    create_time: `2026-07-${String(day).padStart(2, "0")}T09:12:00Z`,
    change_time: `2026-07-${String(Math.min(day + 2, 21)).padStart(2, "0")}T14:30:00Z`,
    age_seconds: (22 - day) * 86400, escalation_time: i % 5 === 0 ? 3600 : 0,
    escalation_response_time: 0, escalation_update_time: 0, escalation_solution_time: 0, until_time: 0,
    attachment_count: ATTACHMENT_TICKET_IDS.has(id) ? (id === 100 || id === 113 ? 2 : 1) : 0,
    has_ai_summary: AI_SUMMARY_TICKET_IDS.has(id),
  };
});
const tickets = { items: ticketItems, total: ticketItems.length, offset: 0, limit: 50 };
const ticketById = new Map(ticketItems.map((t) => [t.id, t]));

function ticketDetailFor(id: number) {
  const item = ticketById.get(id) ?? ticketItems[0];
  return {
    ...item, type_id: 1, service_id: null, sla_id: null, responsible_user_id: 2,
    archive_flag: 0, create_by: 10, change_by: 1,
    dynamic_fields: [
      { name: "Category", label: "Category", field_type: "Dropdown", values: ["Hardware"] },
      { name: "Impact", label: "Impact", field_type: "Dropdown", values: ["High"] },
    ],
  };
}

// ── Per-ticket article threads ──────────────────────────────────────────────
// Each ticket (100-113) gets its own short, realistic thread instead of every
// ticket silently reusing ticket 100's printer thread. Article ids are
// namespaced per ticket (500 + (ticketId-100)*10 .. +9) so they never collide.
type ArticleSpec = {
  day: number; hm: string; sender: "customer" | "agent";
  visible?: boolean; subject: string; from: string; to: string;
  body: string; isHtml?: boolean; channel?: number;
  attachments?: { filename: string; content_type: string; content_size: string }[];
};

function baseArticleId(ticketId: number) {
  return 500 + (ticketId - 100) * 10;
}

const articlesByTicket: Record<number, unknown[]> = {};
const bodiesById: Record<number, unknown> = {};
const attachmentsByArticle: Record<number, unknown[]> = {};

function registerThread(ticketId: number, specs: ArticleSpec[]) {
  const base = baseArticleId(ticketId);
  articlesByTicket[ticketId] = specs.map((a, idx) => {
    const id = base + idx;
    const create_time = `2026-07-${String(a.day).padStart(2, "0")}T${a.hm}:00Z`;
    const isHtml = a.isHtml ?? true;
    bodiesById[id] = {
      article_id: id, content_type: isHtml ? "text/html" : "text/plain", is_html: isHtml,
      body: a.body,
    };
    if (a.attachments) {
      attachmentsByArticle[id] = a.attachments.map((att, ai) => ({
        id: id * 10 + ai, article_id: id, filename: att.filename, content_type: att.content_type,
        content_size: att.content_size, content_id: null, disposition: "attachment", inline: false,
      }));
    }
    return {
      id, ticket_id: ticketId, sender_type: a.sender, sender_type_id: a.sender === "customer" ? 3 : 1,
      communication_channel_id: a.channel ?? 1, is_visible_for_customer: a.visible ?? true,
      create_time, create_by: a.sender === "customer" ? 10 : 1,
      subject: a.subject, from_address: a.from, to_address: a.to,
      content_type: isHtml ? "text/html" : "text/plain",
      incoming_time: Math.floor(Date.parse(create_time) / 1000),
    };
  });
  return articlesByTicket[ticketId];
}

const html = (...paragraphs: string[]) => paragraphs.map((p) => `<p>${p}</p>`).join("");

registerThread(100, [
  { day: 10, hm: "09:12", sender: "customer", subject: "Printer offline in building A",
    from: "j.doe@acme.example", to: "support@example.com",
    body: html(
      "Hi team, the main printer on floor 2 (building A) is offline since this morning. It shows a blinking amber light and won't respond. Several people can't print. Could you take a look?",
      "Thanks,<br>Jane",
    ),
    attachments: [{ filename: "floor2-printer-photo.jpg", content_type: "image/jpeg", content_size: "184320" }] },
  { day: 10, hm: "10:02", sender: "agent", subject: "Re: Printer offline in building A",
    from: "support@example.com", to: "j.doe@acme.example",
    body: html(
      "Hi Jane,",
      "Thanks for reporting. We've reset the print spooler and pushed a firmware update. Could you try again and let us know?",
      "Best,<br>Alex — IT Support",
    ),
    attachments: [{ filename: "printer-error-log.txt", content_type: "text/plain", content_size: "4096" }] },
  { day: 10, hm: "10:05", sender: "agent", visible: false, channel: 3, isHtml: false,
    subject: "Internal note", from: "aturner", to: "",
    body: "Assigned to Level 2 — likely the fuser unit. Ordered a replacement, ETA tomorrow." },
]);

registerThread(101, [
  { day: 11, hm: "09:12", sender: "customer", subject: "VPN access request",
    from: "m.reed@acme.example", to: "support@example.com",
    body: html(
      "Hi team, I'm working from home this week and need VPN access to reach the internal file server and our ticketing system. My manager approved this by email (forwarding separately) — I should be added to the 'Engineering' VPN group. I'm on a company-issued MacBook.",
      "Thanks,<br>Marcus",
    ) },
  { day: 11, hm: "10:30", sender: "agent", subject: "Re: VPN access request",
    from: "support@example.com", to: "m.reed@acme.example",
    body: html(
      "Hi Marcus,",
      "I've added your account to the Engineering VPN group and generated a client profile — see the attached configuration file. Import it into the GlobalProtect client and let us know if the connection succeeds.",
      "One note: our VPN policy requires MFA on first connect, so have your authenticator app ready.",
      "Best,<br>Bianca — IT Support",
    ),
    attachments: [{ filename: "vpn-client-config.ovpn", content_type: "application/octet-stream", content_size: "2048" }] },
  { day: 11, hm: "10:32", sender: "agent", visible: false, channel: 3, isHtml: false,
    subject: "Internal note", from: "bshah", to: "",
    body: "Approved by manager per forwarded email (see ticket history). Added to AD group VPN-Engineering." },
]);

registerThread(102, [
  { day: 12, hm: "09:12", sender: "customer", subject: "Cannot log into portal",
    from: "s.patel@northwind.example", to: "support@example.com",
    body: html(
      "I keep getting \"Invalid credentials\" when logging into the customer portal, even after resetting my password twice. Other pages on our site work fine, just the portal login.",
      "Regards,<br>Sara",
    ) },
  { day: 12, hm: "10:15", sender: "agent", subject: "Re: Cannot log into portal",
    from: "support@example.com", to: "s.patel@northwind.example",
    body: html(
      "Hi Sara,",
      "Found it — your account had a leftover lock from too many failed attempts before the password resets took effect. I've cleared the lock and confirmed you can log in now with your latest password. Let us know if it happens again.",
      "Best,<br>Chris — IT Support",
    ) },
]);

registerThread(103, [
  { day: 13, hm: "09:12", sender: "customer", subject: "Invoice discrepancy for March",
    from: "l.gomez@northwind.example", to: "support@example.com",
    body: html(
      "Hello, I'm reviewing our March invoice (INV-2026-0317) and the line item for \"Additional user licenses\" shows 12 seats, but per our contract we only added 8 additional seats in March. Could someone check billing on this? I've attached a copy of the invoice with the discrepancy highlighted.",
      "Regards,<br>Luis Gomez<br>Northwind Traders",
    ),
    attachments: [{ filename: "invoice-march-2026.pdf", content_type: "application/pdf", content_size: "98304" }] },
  { day: 13, hm: "11:40", sender: "agent", subject: "Re: Invoice discrepancy for March",
    from: "support@example.com", to: "l.gomez@northwind.example",
    body: html(
      "Hi Luis,",
      "Thanks for flagging this — you're right, there's a discrepancy. Looking at our provisioning log, 4 of those seats were added under a separate trial that should have been billed at $0 during the trial period. I'm issuing a credit note for the difference and will forward it within 2 business days.",
      "Apologies for the confusion.",
      "Best,<br>Alex — Billing Support",
    ) },
]);

registerThread(104, [
  { day: 14, hm: "09:12", sender: "customer", subject: "New laptop provisioning",
    from: "k.wu@globex.example", to: "support@example.com",
    body: html(
      "Hi, we have a new hire starting Monday (Priya Shah, Marketing) and she'll need a standard laptop provisioned with the usual software bundle plus Adobe Creative Cloud. Could you get one ready and shipped to our Austin office?",
    ) },
  { day: 14, hm: "13:20", sender: "agent", subject: "Re: New laptop provisioning",
    from: "support@example.com", to: "k.wu@globex.example",
    body: html(
      "Hi Karen,",
      "Happy to help. I've queued a standard MacBook Air build with the marketing software bundle and added an Adobe Creative Cloud license. It'll ship to Austin by Thursday with tracking sent to you directly.",
      "Let us know if she needs anything else set up before day one.",
      "Best,<br>Bianca — IT Support",
    ) },
]);

registerThread(105, [
  { day: 15, hm: "08:41", sender: "customer", subject: "Email delivery delayed",
    from: "t.hall@initech.example", to: "support@example.com",
    body: html(
      "Hi, several of our staff have reported that outbound emails to external clients are taking 20-30 minutes to arrive today, sometimes longer. Internal mail seems fine. Can you check if there's an issue with the mail relay?",
    ) },
  { day: 15, hm: "09:30", sender: "agent", visible: false, channel: 3, isHtml: false,
    subject: "Internal note", from: "cmorris", to: "",
    body: "Outbound queue on mail-relay-02 backed up ~09:15 after a burst of large attachments from a marketing send. Throttled the job and draining the backlog; monitoring queue depth." },
  { day: 15, hm: "10:05", sender: "agent", subject: "Re: Email delivery delayed",
    from: "support@example.com", to: "t.hall@initech.example",
    body: html(
      "Hi Tom,",
      "Thanks for reporting — we saw the same delay on our end. The outbound queue on mail-relay-02 backed up around 09:15 due to a burst of large attachments from a marketing send. We've throttled that job and drained the backlog; delivery times are back to normal.",
      "We'll keep an eye on it and follow up if it recurs.",
      "Best,<br>Chris — IT Support",
    ) },
]);

registerThread(106, [
  { day: 16, hm: "09:12", sender: "customer", subject: "Password reset for shared mailbox",
    from: "j.doe@acme.example", to: "support@example.com",
    body: html(
      "Hi, could someone reset the password for the shared \"orders@acme.example\" mailbox? A few of us use it and nobody remembers the current password after the last rotation.",
    ) },
  { day: 16, hm: "09:50", sender: "agent", subject: "Re: Password reset for shared mailbox",
    from: "support@example.com", to: "j.doe@acme.example",
    body: html(
      "Hi Jane,",
      "Done — I've reset the password for orders@acme.example and sent the new credentials to you and Marcus separately via our secure notes link (expires in 24h). Please update it in Outlook for anyone with delegated access.",
      "Best,<br>Alex — IT Support",
    ) },
]);

registerThread(107, [
  { day: 17, hm: "09:12", sender: "customer", subject: "Website contact form broken",
    from: "m.reed@acme.example", to: "support@example.com",
    body: html(
      "The contact form on our marketing site (acme.example/contact) isn't sending submissions — customers report clicking Send and nothing happens, no confirmation page either. Can someone take a look? This is costing us leads.",
    ) },
  { day: 17, hm: "12:05", sender: "agent", subject: "Re: Website contact form broken",
    from: "support@example.com", to: "m.reed@acme.example",
    body: html(
      "Hi Marcus,",
      "Found it — the form's submit endpoint was pointing at an SSL certificate that expired yesterday. I've renewed the cert and confirmed a test submission goes through and lands in the sales inbox. Could you double-check on your end as well?",
      "Best,<br>Bianca — IT Support",
    ) },
]);

registerThread(108, [
  { day: 18, hm: "08:41", sender: "customer", subject: "Slow database queries",
    from: "s.patel@northwind.example", to: "support@example.com",
    body: html(
      "Hi, our portal has been noticeably slow since this morning — pages that usually load instantly are taking 5-10 seconds, and a couple of colleagues got timeout errors. Is something wrong on your end?",
    ) },
  { day: 18, hm: "08:50", sender: "agent", visible: false, channel: 3, isHtml: false,
    subject: "Internal note", from: "cmorris", to: "",
    body: "Monitoring alert fired for web-prod-03: CPU 94%, HTTP latency p95 2.4s, correlates with the slow queries reported by the customer. Investigating; no customer reply sent yet pending root cause." },
]);

registerThread(109, [
  { day: 19, hm: "09:12", sender: "customer", subject: "Request: additional license seats",
    from: "l.gomez@northwind.example", to: "support@example.com",
    body: html(
      "We're onboarding three new analysts next month and will need three additional seats on our reporting license. Can you add these to our current subscription and let me know the updated invoice amount?",
    ) },
  { day: 19, hm: "11:00", sender: "agent", subject: "Re: Request: additional license seats",
    from: "support@example.com", to: "l.gomez@northwind.example",
    body: html(
      "Hi Luis,",
      "Added three seats to your reporting license, effective immediately. The prorated charge for this billing cycle will show as a separate line on next month's invoice; going forward it's part of the recurring total.",
      "Best,<br>Alex — Billing Support",
    ) },
]);

registerThread(110, [
  { day: 20, hm: "09:12", sender: "customer", subject: "Two-factor app not accepting codes",
    from: "k.wu@globex.example", to: "support@example.com",
    body: html(
      "My authenticator app stopped working — it's generating codes that the portal rejects as invalid. I didn't change phones or reinstall anything. I'm locked out of my account now.",
    ) },
  { day: 20, hm: "09:45", sender: "agent", subject: "Re: Two-factor app not accepting codes",
    from: "support@example.com", to: "k.wu@globex.example",
    body: html(
      "Hi Karen,",
      "This usually means the phone's clock drifted out of sync with our server, which throws off the time-based codes. I've reset your 2FA enrollment — please re-scan the QR code we're sending to your registered email, and check that \"Set time automatically\" is enabled on your phone.",
      "Best,<br>Bianca — IT Support",
    ) },
]);

registerThread(111, [
  { day: 21, hm: "09:12", sender: "customer", subject: "Onboarding new starter",
    from: "t.hall@initech.example", to: "support@example.com",
    body: html(
      "New starter Priya Nair joins us next Monday in Finance. Could you provision accounts (email, VPN, finance system access) and send me the checklist so I can confirm everything's ready before her first day? I've attached our standard onboarding checklist for reference.",
    ),
    attachments: [{ filename: "onboarding-checklist.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", content_size: "31744" }] },
  { day: 21, hm: "14:00", sender: "agent", subject: "Re: Onboarding new starter",
    from: "support@example.com", to: "t.hall@initech.example",
    body: html(
      "Hi Tom,",
      "All set — email and VPN accounts are provisioned, and I've submitted the finance-system access request to that team (usually a 1-day turnaround).",
      "Best,<br>Chris — IT Support",
    ) },
]);

registerThread(112, [
  { day: 10, hm: "09:12", sender: "customer", subject: "Firewall rule change request",
    from: "j.doe@acme.example", to: "support@example.com",
    body: html(
      "We need a firewall rule opened to allow our new analytics vendor (203.0.113.44) to reach our reporting API on port 8443. Can this go through change control this week?",
    ) },
  { day: 10, hm: "15:30", sender: "agent", subject: "Re: Firewall rule change request",
    from: "support@example.com", to: "j.doe@acme.example",
    body: html(
      "Hi Jane,",
      "Submitted through change control and scheduled for tonight's maintenance window. I'll open 203.0.113.44 to port 8443 on the reporting API only, restricted to that single source IP. You'll get a confirmation once it's live and tested.",
      "Best,<br>Alex — IT Support",
    ) },
]);

registerThread(113, [
  { day: 11, hm: "06:05", sender: "agent", visible: false, channel: 3, isHtml: false,
    subject: "Internal note", from: "bshah", to: "",
    body: "Nightly backup job for file-server-02 failed at 02:14 with error \"target volume unreachable\". Retried automatically at 03:00 — failed again. Escalating for manual review.",
    attachments: [{ filename: "backup-error-screenshot.png", content_type: "image/png", content_size: "62208" }] },
  { day: 11, hm: "09:00", sender: "customer", subject: "Backup job failed overnight",
    from: "m.reed@acme.example", to: "support@example.com",
    body: html(
      "Just noticed the automated alert about last night's backup failure for file-server-02 — can you confirm nothing was actually lost and that tonight's run will succeed?",
    ) },
  { day: 11, hm: "10:00", sender: "agent", subject: "Re: Backup job failed overnight",
    from: "support@example.com", to: "m.reed@acme.example",
    body: html(
      "Hi Marcus,",
      "Confirmed no data loss — this was a backup job failure, not a storage issue. The target NAS had a stale mount after last week's network maintenance; we've remounted it and run a manual backup successfully just now. Tonight's scheduled job should complete normally.",
      "Attached is the job log from last night for your records.",
      "Best,<br>Bianca — IT Support",
    ),
    attachments: [{ filename: "backup-job-log.txt", content_type: "text/plain", content_size: "12288" }] },
]);

// AI subsystem (state-only summary + drafts) for the demo ticket. Static,
// fabricated content so the public demo showcases the assistant end-to-end
// without a live LLM: a coverage-tracked summary and two open drafts (an
// auto-generated reply plus a manual clarification), each with a tool trace.
const ticketAiSummary =
  "Jane Doe reported that the main printer on floor 2 (building A) has been offline " +
  "since this morning — a blinking amber light, no response, and several colleagues " +
  "unable to print. IT reset the print spooler and pushed a firmware update, then " +
  "asked Jane to retry. Level 2 suspects a failing fuser unit and has already ordered " +
  "a replacement (ETA tomorrow) as a fallback.\n\n" +
  "Open point: awaiting Jane's confirmation that printing works after the firmware " +
  "update before closing the ticket or swapping the hardware.\n\n" +
  "Dokumente:\n" +
  "- printer-error-log.txt — spooler restart and firmware-update trace\n" +
  "- floor2-printer-photo.jpg — amber status light on the device";

const ticketAiDrafts = [
  {
    id: 9001, ticket_id: 100, kind: "reply",
    subject: "Re: Printer offline in building A",
    body:
      "Hi Jane,\n\nThanks for your patience. We've reset the print spooler and installed " +
      "a firmware update on the floor 2 printer. Could you try printing again and let us " +
      "know whether the amber light has cleared?\n\nIf it's still offline, we already have " +
      "a replacement fuser unit on the way (arriving tomorrow) and will swap it first thing.\n\n" +
      "Best regards,\nIT Support",
    based_on_article_id: 500, status: "open", source: "auto",
    accepted_article_id: null, create_time: "2026-07-10T10:07:00Z",
    tool_trace: [
      { name: "kb.search", content: 'Matched KB article "Printer shows amber light / offline" — fuser-unit troubleshooting and spooler-reset steps.' },
      { name: "ticket.history", content: "Firmware update pushed at 10:02; Level 2 ordered a replacement fuser unit (ETA +1 day)." },
    ],
  },
  {
    id: 9002, ticket_id: 100, kind: "clarify",
    subject: null,
    body:
      "To narrow this down before the replacement part arrives: after the firmware update, " +
      "does the printer show any error code on its display, or only the blinking amber light? " +
      "A quick photo of the panel would help us confirm whether it's the fuser unit.",
    based_on_article_id: 500, status: "open", source: "manual",
    accepted_article_id: null, create_time: "2026-07-10T10:09:00Z",
    tool_trace: [],
  },
];

const ticketAiState = {
  manual_assist_available: true,
  summary_available: true,
  can_summarize: true,
  operation_mode_ready: true,
  drafts: ticketAiDrafts,
  summary_body: ticketAiSummary,
  last_summary_upto_article_id: 502,
  summary_created_at: "2026-07-10T10:06:00Z",
};

// A second AI scenario (ticket 108, "Slow database queries") showcasing MCP
// tool use: the assistant pulled live readings from a monitoring MCP server
// and grounded its draft in them. The tool_trace `content` is JSON so the
// panel renders it as a compact key/value table (arrays become pill badges).
const ticketAiSummaryMcp =
  "The monitoring integration flagged web-prod-03 with sustained high load and slow " +
  "HTTP responses since 08:41, matching the query slowness Sara Patel reported. Live " +
  "metrics pulled through the monitoring MCP server confirm CPU at 94%, a 15-minute " +
  "load average of 11.8, and memory at 88%, with three alerts currently firing " +
  "(CPUHigh, HTTPLatencyP95, and DiskWillFill on /var). The assistant grounded its " +
  "customer reply in those live readings rather than guessing.\n\n" +
  "Open point: confirm whether the traffic spike is organic before scaling out versus " +
  "rolling back this morning's deploy.";

const ticketAiDraftsMcp = [
  {
    id: 9101, ticket_id: 108, kind: "reply",
    subject: "Re: Slow database queries",
    body:
      "Hi Sara,\n\nThanks for flagging this. Our monitoring confirms one of the web nodes " +
      "(web-prod-03) is under heavy load right now — CPU around 94% and elevated request " +
      "latency since 08:41. We're shifting traffic off that node and adding capacity; you " +
      "should see response times recover within the next few minutes. We'll follow up here " +
      "once it's fully stable.\n\nBest regards,\nOperations",
    based_on_article_id: baseArticleId(108), status: "open", source: "auto",
    accepted_article_id: null, create_time: "2026-07-18T08:52:00Z",
    tool_trace: [
      {
        name: "monitoring.get_host_metrics",
        content: JSON.stringify({
          host: "web-prod-03",
          cpu_pct: 94,
          load_avg_15m: 11.8,
          mem_used_pct: 88,
          swap_used_pct: 37,
          uptime: "42d 6h",
        }),
      },
      {
        name: "monitoring.list_active_alerts",
        content: JSON.stringify({
          host: "web-prod-03",
          firing: [
            "CPUHigh 94% > 85% (12m)",
            "HTTPLatencyP95 2.4s > 1s",
            "DiskWillFill /var ~6h",
          ],
          since: "2026-07-18T08:41:00Z",
        }),
      },
    ],
  },
];

const ticketAiStateMcp = {
  manual_assist_available: true,
  summary_available: true,
  can_summarize: true,
  operation_mode_ready: true,
  drafts: ticketAiDraftsMcp,
  summary_body: ticketAiSummaryMcp,
  last_summary_upto_article_id: baseArticleId(108) + 1,
  summary_created_at: "2026-07-18T08:51:00Z",
};

// A third AI scenario (ticket 105, "Email delivery delayed"): a lighter-weight
// summary built purely from ticket history (no MCP tool calls), showing the
// assistant can summarize without external grounding.
const ticketAiSummaryMail =
  "Tom Hall reported outbound email delays of 20-30 minutes to external recipients, " +
  "starting around 08:41. IT traced it to the outbound queue on mail-relay-02 " +
  "backing up after a burst of large attachments from a marketing send, throttled " +
  "the job, and drained the backlog; delivery times are back to normal.\n\n" +
  "Open point: none — reply confirming the fix is ready to send.";

const ticketAiDraftsMail = [
  {
    id: 9201, ticket_id: 105, kind: "reply",
    subject: "Re: Email delivery delayed",
    body:
      "Hi Tom,\n\nThanks for reporting — we saw the same delay on our end. The outbound " +
      "queue on mail-relay-02 backed up around 09:15 due to a burst of large attachments " +
      "from a marketing send. We've throttled that job and drained the backlog; delivery " +
      "times are back to normal.\n\nWe'll keep an eye on it and follow up if it recurs.\n\n" +
      "Best regards,\nIT Support",
    based_on_article_id: baseArticleId(105), status: "open", source: "auto",
    accepted_article_id: null, create_time: "2026-07-15T09:35:00Z",
    tool_trace: [
      { name: "ticket.history", content: "Internal note at 09:30 confirms mail-relay-02 queue backlog identified and job throttled." },
    ],
  },
];

const ticketAiStateMail = {
  manual_assist_available: true,
  summary_available: true,
  can_summarize: true,
  operation_mode_ready: true,
  drafts: ticketAiDraftsMail,
  summary_body: ticketAiSummaryMail,
  last_summary_upto_article_id: baseArticleId(105) + 1,
  summary_created_at: "2026-07-15T09:34:00Z",
};

// Tickets without a designated AI scenario: manual assist only, no summary
// yet generated — this is what backs the ✦ icon's absence in the queue row.
const ticketAiStateUnavailable = {
  manual_assist_available: true,
  summary_available: false,
  can_summarize: true,
  operation_mode_ready: true,
  drafts: [],
  summary_body: null,
  last_summary_upto_article_id: null,
  summary_created_at: null,
};

const aiStateByTicket: Record<number, unknown> = {
  100: ticketAiState,
  105: ticketAiStateMail,
  108: ticketAiStateMcp,
};

// Admin AI config so the demo's AI settings/providers pages look provisioned
// (rather than an empty "not configured" state).
const aiSettings = {
  operation_mode: "tiqora_primary",
  disclosure_default_text:
    "This reply was drafted with AI assistance and reviewed by a support agent.",
  global_max_replies_per_hour: 60,
  audit_retention_days: 90,
  auto_reply_paused: false,
};
const aiProviders = [
  {
    id: 1, name: "OpenAI (GPT-4o)", kind: "openai_compat", base_url: "https://api.openai.com/v1",
    default_model: "gpt-4o", has_api_key: true, extra_json: null, supports_tools: true,
    supports_streaming: true, eu_hosted: false, supports_vision: true,
    price_input_per_1m: 2.5, price_output_per_1m: 10, price_currency: "USD",
    valid_id: 1, create_time: t0, change_time: t0,
  },
  {
    id: 2, name: "Anthropic (Claude Sonnet)", kind: "anthropic", base_url: "https://api.anthropic.com",
    default_model: "claude-sonnet-4", has_api_key: true, extra_json: null, supports_tools: true,
    supports_streaming: true, eu_hosted: false, supports_vision: true,
    price_input_per_1m: 3, price_output_per_1m: 15, price_currency: "USD",
    valid_id: 1, create_time: t0, change_time: t0,
  },
];

const searchHits = {
  query: "server", estimated_total: 4,
  hits: ticketItems.slice(0, 4).map((t) => ({ id: t.id, tn: t.tn, title: t.title, queue_id: t.queue_id, queue_name: t.queue_name, state: t.state, state_type: t.state_type, priority: t.priority, owner_login: t.owner_login, customer_user_id: t.customer_user_id, create_time: t.create_time, excerpt: "…matched on the ticket subject and latest article…" })),
};

// KB categories match CategoryOut; articles match ArticleSummary[] (bare array).
const kbCategories = [
  { id: 1, name: "Getting started", slug: "getting-started", parent_id: null, sort: 10, valid: true, customer_visible: true, permission_group_ids: [2], create_time: t0, change_time: "2026-07-15T10:00:00Z" },
  { id: 2, name: "Email & calendar", slug: "email-calendar", parent_id: null, sort: 20, valid: true, customer_visible: true, permission_group_ids: [2], create_time: t0, change_time: "2026-07-14T09:00:00Z" },
  { id: 3, name: "VPN & remote access", slug: "vpn-remote", parent_id: null, sort: 30, valid: true, customer_visible: true, permission_group_ids: [2], create_time: t0, change_time: "2026-07-14T09:00:00Z" },
  { id: 4, name: "Printing", slug: "printing", parent_id: null, sort: 40, valid: true, customer_visible: false, permission_group_ids: [2], create_time: t0, change_time: "2026-07-18T16:00:00Z" },
];
const kbArticleItems = [
  { id: 700, title: "How to reset your password", slug: "how-to-reset-your-password", category_id: 1, state: "published", language: "en", version: 3, change_time: "2026-07-15T10:00:00Z" },
  { id: 701, title: "Setting up the VPN client", slug: "setting-up-the-vpn-client", category_id: 3, state: "published", language: "en", version: 2, change_time: "2026-07-14T09:00:00Z" },
  { id: 702, title: "Sharing a mailbox", slug: "sharing-a-mailbox", category_id: 2, state: "published", language: "en", version: 1, change_time: "2026-07-12T08:00:00Z" },
  { id: 703, title: "Fixing common printer problems", slug: "fixing-common-printer-problems", category_id: 4, state: "draft", language: "en", version: 1, change_time: "2026-07-18T16:00:00Z" },
  { id: 704, title: "Requesting a new laptop", slug: "requesting-a-new-laptop", category_id: 1, state: "published", language: "en", version: 1, change_time: "2026-07-11T11:00:00Z" },
  { id: 705, title: "Calendar sharing best practices", slug: "calendar-sharing-best-practices", category_id: 2, state: "review", language: "en", version: 2, change_time: "2026-07-16T14:00:00Z" },
];
const kbArticleDetail = {
  ...kbArticleItems[0],
  body_md: "# How to reset your password\n\n1. Open the portal and choose **Forgot password**.\n2. Enter your work email.\n3. Follow the link we send you (valid for 30 minutes).\n4. Choose a new password that meets the complexity rules.\n\nIf you still cannot sign in, open a ticket with the **Support** queue.",
  tags: ["password", "sso", "onboarding"],
  customer_visible: true,
  create_time: t0,
  create_by: 1,
  change_by: 1,
};

const historyEntries = [
  { id: 9001, ticket_id: 100, history_type_id: 1, history_type: "NewTicket", name: "%%Printer offline in building A%%", rendered: "Created ticket", create_by: 10, create_by_login: "j.doe@acme.example", create_time: "2026-07-10T09:12:00Z", owner_id: 1, article_id: 500 },
  { id: 9002, ticket_id: 100, history_type_id: 27, history_type: "OwnerUpdate", name: "%%aturner%%", rendered: "Owner set to aturner", create_by: 1, create_by_login: "aturner", create_time: "2026-07-10T09:40:00Z", owner_id: 1, article_id: null },
  { id: 9003, ticket_id: 100, history_type_id: 8, history_type: "StateUpdate", name: "%%new%%open%%", rendered: "State changed from new to open", create_by: 1, create_by_login: "aturner", create_time: "2026-07-10T09:41:00Z", owner_id: 1, article_id: null },
  { id: 9004, ticket_id: 100, history_type_id: 19, history_type: "AddNote", name: "%%Internal note%%", rendered: "Added note", create_by: 1, create_by_login: "aturner", create_time: "2026-07-10T10:05:00Z", owner_id: 1, article_id: 502 },
];

const onlineAgents = [
  { id: 1, login: "aturner", full_name: "Alex Turner", avatar_url: null },
  { id: 2, login: "bshah", full_name: "Bianca Shah", avatar_url: null },
  { id: 3, login: "cmorris", full_name: "Chris Morris", avatar_url: null },
];

// ── Mentions + time accounting (header counters, report page) ───────────────
const ticketMentions = [
  { id: 700, ticket_id: 100, user_id: 2, user_name: "Bianca Shah", user_login: "bshah", create_time: "2026-07-10T10:06:00Z" },
];
const ticketTimeEntries = [
  { id: 710, ticket_id: 100, time_unit: 15, create_by: 1, create_by_login: "aturner", create_time: "2026-07-10T10:05:00Z" },
  { id: 711, ticket_id: 100, time_unit: 7.5, create_by: 2, create_by_login: "bshah", create_time: "2026-07-11T14:20:00Z" },
];
// Spread across several days so the report's units-per-day bars have shape.
const timeAccountingRows = [
  { id: 710, ticket_id: 100, ticket_tn: "2026071000100", ticket_title: "Printer offline in building A", time_unit: 15, create_by: 1, create_by_login: "aturner", create_time: "2026-07-10T10:05:00Z" },
  { id: 711, ticket_id: 100, ticket_tn: "2026071000100", ticket_title: "Printer offline in building A", time_unit: 7.5, create_by: 2, create_by_login: "bshah", create_time: "2026-07-11T14:20:00Z" },
  { id: 712, ticket_id: 101, ticket_tn: "2026071000101", ticket_title: "VPN drops every few minutes", time_unit: 30, create_by: 1, create_by_login: "aturner", create_time: "2026-07-11T16:02:00Z" },
  { id: 713, ticket_id: 102, ticket_tn: "2026071200102", ticket_title: "New starter needs a mailbox", time_unit: 12, create_by: 3, create_by_login: "cmorris", create_time: "2026-07-12T09:15:00Z" },
  { id: 714, ticket_id: 101, ticket_tn: "2026071000101", ticket_title: "VPN drops every few minutes", time_unit: 45, create_by: 1, create_by_login: "aturner", create_time: "2026-07-14T11:40:00Z" },
  { id: 715, ticket_id: 103, ticket_tn: "2026071500103", ticket_title: "Invoice export fails", time_unit: 20, create_by: 2, create_by_login: "bshah", create_time: "2026-07-15T13:05:00Z" },
];
const timeAccountingReport = {
  items: timeAccountingRows,
  total_units: timeAccountingRows.reduce((sum, r) => sum + r.time_unit, 0),
};

// ── Stats (14-day series, richer) ───────────────────────────────────────────
const days = Array.from({ length: 14 }, (_, i) => `2026-07-${String(i + 8).padStart(2, "0")}`);
const volume = { granularity: "day", points: days.map((d, i) => ({ bucket: d, created: 6 + ((i * 3) % 7), closed: 4 + ((i * 2) % 6) })) };
const backlog = { granularity: "day", points: days.map((d, i) => ({ bucket: d, open_count: 40 + ((i * 5) % 18) })) };
const openSnapshot = { dimension: "queue", items: [
  { id: 2, label: "Support::Level 1", count: 13 }, { id: 3, label: "Support::Level 2", count: 6 },
  { id: 4, label: "Incidents", count: 12 }, { id: 5, label: "Sales", count: 9 }, { id: 6, label: "Billing", count: 8 },
] };
const sla = { total: 58, escalated: 6, first_response_breached: 3, update_breached: 1, solution_breached: 2,
  first_response_minutes: [12, 20, 35, 44, 51, 63, 22, 18], solution_minutes: [120, 240, 90, 310, 150] };
const agentWorkload = [
  { user_id: 1, login: "aturner", name: "Alex Turner", owned_open: 9, closed_in_period: 21 },
  { user_id: 2, login: "bshah", name: "Bianca Shah", owned_open: 7, closed_in_period: 18 },
  { user_id: 3, login: "cmorris", name: "Chris Morris", owned_open: 5, closed_in_period: 14 },
  { user_id: 4, login: "dpark", name: "Dana Park", owned_open: 4, closed_in_period: 11 },
];

const calendars = [{ id: 1, name: "Support on-call", color: "#2563eb", valid_id: 1 }];
const occurrences = [
  { id: 800, calendar_id: 1, title: "On-call: Alex", start_time: "2026-07-20T08:00:00Z", end_time: "2026-07-20T17:00:00Z", all_day: false, location: "Remote" },
  { id: 801, calendar_id: 1, title: "Maintenance window", start_time: "2026-07-22T22:00:00Z", end_time: "2026-07-23T02:00:00Z", all_day: false, location: "DC-1" },
];

// ── Admin data (envelope shape) ─────────────────────────────────────────────
const adminUsers = [
  ["aturner", "Alex", "Turner"], ["bshah", "Bianca", "Shah"], ["cmorris", "Chris", "Morris"],
  ["dpark", "Dana", "Park"], ["efox", "Erin", "Fox"], ["gliu", "Grace", "Liu"],
  ["hkaur", "Harpreet", "Kaur"], ["iowens", "Ivan", "Owens"],
].map(([login, f, l], i) => ({ id: i + 1, login, title: null, first_name: f, last_name: l, valid_id: i === 7 ? 2 : 1, create_time: t0, change_time: t0 }));
const adminGroups = [
  ["admin", "Administrators"], ["users", "All agents"], ["support", "Support team"],
  ["incidents", "Incident response"], ["sales", "Sales team"], ["billing", "Billing team"],
].map(([name, comments], i) => ({ id: i + 1, name, comments, valid_id: 1, create_time: t0, change_time: t0 }));
const adminRoles = [
  ["Agent", "Standard agent"], ["Supervisor", "Team lead / supervisor"],
  ["Read-only", "Read-only auditor"], ["Admin", "Full administrator"],
].map(([name, comments], i) => ({ id: i + 1, name, comments, valid_id: 1, create_time: t0, change_time: t0 }));
const adminQueuesFull = agentQueues.flatMap((q) => [q, ...q.children]).map((q) => ({
  id: q.id, name: q.name, group_id: q.group_id, unlock_timeout: 1440, first_response_time: 60,
  first_response_notify: 80, update_time: null, update_notify: null, solution_time: 480, solution_notify: 90,
  system_address_id: 1, calendar_name: null, default_sign_key: null, salutation_id: 1, signature_id: 1,
  follow_up_id: 1, follow_up_lock: 0, comments: null, valid_id: 1, create_time: t0, change_time: t0,
}));
const adminDynFields = [
  ["Category", "Dropdown", "Ticket"], ["Impact", "Dropdown", "Ticket"], ["Urgency", "Dropdown", "Ticket"],
  ["AssetTag", "Text", "Ticket"], ["ResolutionCode", "Dropdown", "Ticket"], ["CustomerSatisfaction", "Dropdown", "Ticket"],
].map(([name, ft, ot], i) => ({ id: i + 1, internal_field: 0, name, label: name, field_order: i + 1, field_type: ft, object_type: ot, config: {}, valid_id: 1, create_time: t0, change_time: t0 }));
const adminCustomerUsers = CUSTOMERS.map((c, i) => ({ id: i + 1, login: c.login, email: c.login, customer_id: c.cid, title: null, first_name: c.name.split(" ")[0], last_name: c.name.split(" ")[1], valid_id: 1, create_time: t0, change_time: t0 }));
const adminCustomerCompanies = [
  ["ACME", "ACME Corporation"], ["NORTHWIND", "Northwind Traders"], ["GLOBEX", "Globex Inc."], ["INITECH", "Initech LLC"],
].map(([customer_id, name], i) => ({ id: i + 1, customer_id, name, street: "1 Market St", zip: "90210", city: "Springfield", country: "US", url: null, comments: null, valid_id: 1, create_time: t0, change_time: t0 }));

// 2FA / auth config
const authConfigAgents = adminUsers.map((u, i) => ({
  user_id: u.id, login: u.login, name: `${u.first_name} ${u.last_name}`,
  totp_enabled: i % 2 === 0, webauthn_enabled: i % 3 === 0, passkey_count: i % 3 === 0 ? 1 : 0,
  two_factor_enabled: i % 2 === 0 || i % 3 === 0, sso_eligible: i % 4 === 0, enforce_2fa: i < 3,
  valid_id: u.valid_id,
}));

// GDPR preview
const gdprPreview = {
  mode: "anonymize",
  customers: CUSTOMERS.slice(0, 4).map((c, i) => ({ id: i + 1, login: c.login, email: c.login, customer_id: c.cid, first_name: c.name.split(" ")[0], last_name: c.name.split(" ")[1], valid_id: 1 })),
  counts: { customer_user: 4, customer_company: 2, ticket: 37, article: 214, article_data_mime: 214, customer_preferences: 12, customer_user_customer: 6, group_customer_user: 3 },
  sample: [], columns_changed: { customer_user: ["first_name", "last_name", "email", "phone"], ticket: ["customer_user_id"] }, tables_deleted: [],
};

// group/role assignment membership (for the assignment editors)
const groupCustomerUsers: Record<number, unknown[]> = { 3: [ { login: CUSTOMERS[0].login, permission: "rw" }, { login: CUSTOMERS[2].login, permission: "ro" } ] };
const customerUserGroups: Record<string, unknown[]> = { [CUSTOMERS[0].login]: [ { group_id: 3, name: "support", permission: "rw" } ] };
const groupUsers: Record<number, unknown[]> = { 3: [ { user_id: 1, login: "aturner", permission: "rw" }, { user_id: 2, login: "bshah", permission: "rw" } ] };

// Portal
export const demoPortalUser = { login: CUSTOMERS[0].login, customer_id: "ACME", first_name: "Jane", last_name: "Doe", email: CUSTOMERS[0].login };
export const demoPortalTickets = { items: ticketItems.slice(0, 4).map((t) => ({ id: t.id, tn: t.tn, title: t.title, state: t.state, state_type: t.state_type, queue_name: t.queue_name, create_time: t.create_time, change_time: t.change_time })), total: 4, offset: 0, limit: 50 };

export function resolveData(path: string, method: string): unknown | undefined {
  const p = path;
  // Auth
  if (p.endsWith("/auth/methods")) return { password: true, oidc: false, spnego: false, webauthn: true, ldap: false };
  if (p.endsWith("/auth/me")) return demoUser;
  if (p.endsWith("/auth/login") && method === "POST") return { user: demoUser };
  if (p.endsWith("/auth/logout")) return {};
  if (p.endsWith("/auth/totp")) return { enabled: true, confirmed: true };
  if (p.endsWith("/auth/passkey")) return [{ id: 1, name: "MacBook Touch ID", created: t0, last_used_at: "2026-07-19T08:00:00Z" }];
  // Agent
  if (p.endsWith("/api/v1/queues")) return agentQueues;
  if (p.endsWith("/api/v1/tickets/dashboard-summary")) return { my_open: 9, my_new: 3, unowned_new: 5, escalated: 6 };
  if (p.endsWith("/api/v1/tickets") && method === "GET") return tickets;
  if (p.match(/\/api\/v1\/tickets\/\d+\/articles\/\d+\/body$/)) { const id = Number(p.split("/").slice(-2)[0]); return bodiesById[id] ?? bodiesById[500]; }
  if (p.match(/\/api\/v1\/tickets\/\d+\/articles\/\d+\/attachments/)) { const aid = Number(p.split("/").slice(-2)[0]); return attachmentsByArticle[aid] ?? []; }
  if (p.match(/\/api\/v1\/tickets\/\d+\/articles$/)) { const tid = Number(p.split("/").slice(-2)[0]); return articlesByTicket[tid] ?? []; }
  if (p.match(/\/api\/v1\/tickets\/\d+\/history$/)) return historyEntries;
  if (p.match(/\/api\/v1\/tickets\/\d+\/presence/)) return method === "GET" ? [] : {};
  // Header counters + the cross-ticket report they feed.
  if (p.endsWith("/api/v1/tickets/time-accounting") && method === "GET")
    return timeAccountingReport;
  if (p.match(/\/api\/v1\/tickets\/\d+\/mentions/))
    return method === "GET" ? ticketMentions : { id: 99 };
  if (p.match(/\/api\/v1\/tickets\/\d+\/time-accounting/))
    return method === "GET" ? ticketTimeEntries : { id: 99 };
  if (p.match(/\/api\/v1\/tickets\/\d+\/attachments/) || p.match(/attachments/)) return [];
  // AI subsystem (summary + drafts). The POST endpoints report success; the
  // panel then refetches state, which serves the fabricated summary/drafts.
  if (p.match(/\/api\/v1\/tickets\/\d+\/ai\/summarize$/) && method === "POST")
    return { status: "ok", summary_body: ticketAiSummary, upto_article_id: 502 };
  if (p.match(/\/api\/v1\/tickets\/\d+\/ai\/drafts\/\d+\/discard$/) && method === "POST") return {};
  if (p.match(/\/api\/v1\/tickets\/\d+\/ai\/draft$/) && method === "POST")
    return { status: "ok", draft_id: 9001, article_id: 500 };
  if (p.match(/\/api\/v1\/tickets\/\d+\/ai$/) && method === "GET") {
    const tid = Number(p.split("/").slice(-2)[0]);
    return aiStateByTicket[tid] ?? ticketAiStateUnavailable;
  }
  if (p.match(/\/api\/v1\/tickets\/\d+$/) && method === "GET") { const tid = Number(p.split("/").pop()); return ticketDetailFor(tid); }
  if (p.endsWith("/api/v1/agents/online")) return onlineAgents;
  if (p.endsWith("/api/v1/agents/presence/ping") && method === "POST") return {};
  if (p.endsWith("/api/v1/search")) return searchHits;
  if (p.endsWith("/api/v1/kb/search")) return { query: "", results: kbArticleItems };
  if (p.endsWith("/api/v1/kb/categories")) return kbCategories;
  if (p.match(/\/api\/v1\/kb\/articles\/\d+$/) && method === "GET") return kbArticleDetail;
  if (p.endsWith("/api/v1/kb/articles")) return kbArticleItems;
  if (p.endsWith("/api/v1/kb/assignable-groups")) return [{ id: 2, name: "users" }, { id: 3, name: "support" }];
  if (p.endsWith("/api/v1/stats/volume")) return volume;
  if (p.endsWith("/api/v1/stats/backlog")) return backlog;
  if (p.endsWith("/api/v1/stats/open-snapshot")) return openSnapshot;
  if (p.endsWith("/api/v1/stats/sla")) return sla;
  if (p.endsWith("/api/v1/stats/agent-workload")) return agentWorkload;
  if (p.endsWith("/api/v1/calendar/calendars")) return calendars;
  if (p.endsWith("/api/v1/calendar/appointments")) return occurrences;
  if (p.includes("/api/v1/process")) return method === "GET" ? [] : {};
  // Admin — assignment editors (before the generic list handler)
  if (p.match(/\/admin\/groups\/\d+\/customer-users$/)) { const id = Number(p.split("/").slice(-2)[0]); return groupCustomerUsers[id] ?? []; }
  if (p.match(/\/admin\/groups\/\d+\/users$/)) { const id = Number(p.split("/").slice(-2)[0]); return groupUsers[id] ?? []; }
  if (p.match(/\/admin\/customer-users\/[^/]+\/groups$/)) { const login = decodeURIComponent(p.split("/").slice(-2)[0]); return customerUserGroups[login] ?? []; }
  if (p.match(/\/admin\/users\/\d+\/effective-permissions$/)) return { roles: [], groups: [], queues: [] };
  if (p.match(/\/admin\/users\/\d+\/language$/)) return { language: null };
  // Admin — GDPR
  if (p.endsWith("/admin/gdpr/preview") && method === "POST") return gdprPreview;
  if (p.endsWith("/admin/gdpr/jobs")) return page([]);
  // Admin — auth config (2FA) uses the paginated envelope
  if (p.endsWith("/admin/auth-config")) return page(authConfigAgents);
  // Dynamic fields page consumes a bare array
  if (p.endsWith("/admin/dynamic-fields")) return adminDynFields;
  // Admin — AdminResourcePage lists expect the ENVELOPE
  if (p.endsWith("/admin/users")) return page(adminUsers);
  if (p.endsWith("/admin/groups")) return page(adminGroups);
  if (p.endsWith("/admin/roles")) return page(adminRoles);
  if (p.endsWith("/admin/queues")) return page(adminQueuesFull);
  if (p.endsWith("/admin/customer-users")) return page(adminCustomerUsers);
  if (p.endsWith("/admin/customer-companies")) return page(adminCustomerCompanies);
  if (p.endsWith("/admin/gdpr/jobs")) return page([]);
  // Admin — AI subsystem (settings object + provider list); other AI lists
  // (queue policies, MCP clients) fall through to the empty-array default.
  if (p.endsWith("/admin/ai/settings")) return aiSettings;
  if (p.endsWith("/admin/ai/providers") && method === "GET") return aiProviders;
  // Everything else under /admin (aux lookups: system-addresses, salutations,
  // signatures, states, priorities, …) → bare array so `.map` consumers work.
  if (p.includes("/admin/") && method === "GET") return [];
  return undefined;
}

