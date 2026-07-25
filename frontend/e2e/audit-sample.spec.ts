import { test } from "@playwright/test";
import { buildAuditPdfHtml, type ParsedRequest, type ParsedResponse } from "../src/routes/admin/auditPdf";
import type { AiAuditLogDetailOut } from "../src/lib/aiApi";

/**
 * Generator for the committed sample audit PDF (site/ai-audit-sample.pdf),
 * linked from the public marketing site. Not part of the normal e2e run —
 * gated behind AUDIT_SAMPLE=1 so CI skips it, same pattern as
 * screenshots.spec.ts. No backend or app routes involved: it feeds a
 * masked fixture straight through the real buildAuditPdfHtml() used by
 * AiAuditPage and prints the resulting HTML to PDF.
 *
 *   AUDIT_SAMPLE=1 pnpm exec playwright test e2e/audit-sample.spec.ts --project=chromium
 *
 * Writes ../site/ai-audit-sample.pdf.
 */

test.skip(!process.env.AUDIT_SAMPLE, "gen only — set AUDIT_SAMPLE=1 to run");

// English field labels, taken verbatim from src/i18n/locales/en.json
// (admin.ai.audit.*) so the sample matches the real UI. Hardcoded here to
// keep the generator self-contained.
const labels = {
  title: "Audit entry",
  masked: "PII masked",
  revealed: "Personal data included",
  response: "Response",
  error: "Error",
  generated: "Generated",
  fields: {
    time: "Time",
    feature: "Feature",
    provider: "Provider · Model",
    ticket: "Ticket",
    runId: "Run ID",
    trigger: "Trigger",
    status: "Status",
    tokens: "Tokens (in/out)",
    duration: "Duration",
  },
};

const entry: AiAuditLogDetailOut = {
  id: 4821,
  ts: "2026-07-24T09:14:32.000Z",
  run_id: "run_8f2a1c4e9b3d",
  provider_id: 1,
  provider_name: "OpenAI",
  model: "gpt-4o",
  feature: "auto_reply",
  ticket_id: 20394,
  queue_id: 3,
  acting_user_id: 12,
  trigger: "auto",
  status_code: 200,
  error: null,
  duration_ms: 4820,
  prompt_tokens: 2143,
  completion_tokens: 386,
  pii_counts: { EMAIL: 1, PHONE: 1, IPV4: 1 },
  cost: 0.0187,
  cost_currency: "USD",
  request_json: "",
  response_json: "",
};

const parsedRequest: ParsedRequest = {
  max_tokens: 800,
  temperature: 0.2,
  tools: ["kb.search", "billing.lookup_invoice"],
  messages: [
    {
      role: "system",
      content:
        "You are Tiqora's support assistant. Draft a helpful, empathetic reply for the agent to review. " +
        "Use the ticket context and any tool results provided. Never invent account details — look them up.",
    },
    {
      role: "user",
      content:
        "Ticket #20394 — Subject: Payment failed and now I'm locked out of VPN\n\n" +
        "Customer message:\n" +
        "Hi, I tried to pay my March invoice yesterday and the payment was declined twice, even though the " +
        "card is valid and has plenty of headroom. Since then I can't get onto the company VPN from home " +
        "anymore — it just says \"access denied\" straight away. I need this fixed today because I have a " +
        "client call this afternoon and all my files are on the internal drive.\n\n" +
        "My name is [PERSON_1], you can reach me at [EMAIL_1] or [PHONE_1] if it's easier to call. " +
        "I'm currently connecting from [IP_1] in case that helps. My IBAN on file is [IBAN_1] if you need to " +
        "double check the direct debit.\n\n" +
        "Internal note (agent, not visible to customer):\n" +
        "Billing shows two failed SEPA attempts for invoice INV-2026-03-0117, reason code " +
        "R01 (insufficient funds) both times — worth confirming with the customer whether a second card/" +
        "account was intended. VPN lockout is very likely automatic: our access policy suspends VPN for " +
        "accounts with an overdue invoice > 5 days. Check kb article on payment-triggered lockouts before " +
        "replying, and pull the actual invoice + suspension timestamp so the reply is precise rather than " +
        "generic.",
    },
    {
      role: "assistant",
      content: null,
      tool_calls: [
        {
          id: "call_1",
          name: "kb.search",
          arguments: { query: "VPN access suspended overdue invoice policy" },
        },
        {
          id: "call_2",
          name: "billing.lookup_invoice",
          arguments: { invoice_id: "INV-2026-03-0117" },
        },
      ],
    },
    {
      role: "tool",
      name: "kb.search",
      tool_call_id: "call_1",
      content:
        '{"results": [{"id": "kb-0231", "title": "VPN access and overdue invoices", ' +
        '"excerpt": "VPN access is automatically suspended 5 days after an invoice becomes overdue and is ' +
        'restored within 15 minutes of a successful payment. Manual overrides require a supervisor ticket."}]}',
    },
    {
      role: "tool",
      name: "billing.lookup_invoice",
      tool_call_id: "call_2",
      content:
        '{"invoice_id": "INV-2026-03-0117", "amount": "349.00 EUR", "due_date": "2026-07-05", ' +
        '"status": "overdue", "attempts": [{"ts": "2026-07-23T11:02:00Z", "result": "declined", ' +
        '"reason": "R01 insufficient funds"}, {"ts": "2026-07-23T18:47:00Z", "result": "declined", ' +
        '"reason": "R01 insufficient funds"}], "vpn_suspended_at": "2026-07-10T00:00:00Z"}',
    },
    {
      role: "assistant",
      content:
        "Drafting a reply that explains the automatic link between the overdue invoice and VPN access, " +
        "gives a concrete next step, and offers a same-day path to restore access.",
    },
  ],
};

const parsedResponse: ParsedResponse = {
  model: "gpt-4o",
  finish_reason: "stop",
  content:
    "Hi [PERSON_1],\n\n" +
    "Thanks for flagging both issues together — they're actually connected. Invoice INV-2026-03-0117 " +
    "(EUR 349.00, due 2026-07-05) is overdue, and our access policy automatically suspends VPN 5 days after " +
    "an invoice becomes overdue. That's why the lockout started, and it isn't related to the payment attempts " +
    "themselves.\n\n" +
    "On the payment side: both attempts yesterday were declined with reason R01 (insufficient funds) from the " +
    "card issuer's side, not something on our end. If the card should have headroom, it may be worth checking " +
    "with your bank for a temporary hold or a declined-merchant flag — sometimes recurring SEPA collections " +
    "get blocked after a dispute or a card reissue.\n\n" +
    "To get you back onto the VPN today: once the invoice is paid, access is restored automatically within " +
    "about 15 minutes — no ticket needed. If you'd rather not wait on the card, let me know and I can raise a " +
    "supervisor override for VPN access while the payment is sorted out separately, so you're not blocked for " +
    "this afternoon's call.\n\n" +
    "I'll also flag this with billing in case a second payment method makes sense going forward. " +
    "Let me know which option works and I'll action it right away.\n\n" +
    "Best regards,\nSupport Team",
};

test("generate sample audit PDF", async ({ page }) => {
  const html = buildAuditPdfHtml(
    entry,
    parsedRequest,
    parsedResponse,
    /* piiMap */ null,
    labels,
    "24 Jul 2026, 09:14",
  );
  await page.setContent(html);
  await page.pdf({ path: "../site/ai-audit-sample.pdf", format: "A4", printBackground: true });
});
