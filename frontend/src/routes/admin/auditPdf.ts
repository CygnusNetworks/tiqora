import type { AiAuditLogDetailOut } from "@/lib/aiApi";

// ── PDF export (browser print-to-PDF, no backend dependency) ────────────────
//
// This module is pure (no DOM access beyond building an HTML string) so it
// can be exercised by both AiAuditPage and the committed sample-PDF
// generator in e2e/audit-sample.spec.ts without spinning up the page.

export type WireMessage = {
  role: string;
  content?:
    | string
    | Array<{ type: string; text?: string; image_url?: { url?: string } }>
    | null;
  tool_calls?: Array<{
    id?: string;
    name?: string;
    arguments?: unknown;
    function?: { name?: string; arguments?: string };
  }>;
  tool_call_id?: string;
  name?: string;
};

export type ParsedRequest = {
  messages?: WireMessage[];
  max_tokens?: number;
  temperature?: number;
  tools?: unknown[];
};

export type ParsedResponse = {
  content?: string | null;
  tool_calls?: Array<{ id?: string; name?: string; arguments?: unknown }>;
  finish_reason?: string | null;
  model?: string | null;
};

export function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] as string,
  );
}

/** Substitute `[KIND_n]` PII tokens with their real values when a map is given. */
export function applyPiiMap(
  text: string,
  map: Record<string, string> | null,
): string {
  if (!map) return text;
  let out = text;
  for (const [token, real] of Object.entries(map)) out = out.split(token).join(real);
  return out;
}

export function messageContentText(m: WireMessage): string {
  if (typeof m.content === "string") return m.content;
  if (Array.isArray(m.content)) {
    return m.content
      .map((part) =>
        part.type === "text"
          ? (part.text ?? "")
          : part.type === "image_url"
            ? `[${part.image_url?.url ?? "image"}]`
            : "",
      )
      .join("\n");
  }
  return "";
}

const roleColor: Record<string, string> = {
  system: "#6b7280",
  user: "#2563eb",
  assistant: "#059669",
  tool: "#b45309",
  error: "#dc2626",
};

export type PdfLabels = {
  title: string;
  masked: string;
  revealed: string;
  fields: Record<string, string>;
  response: string;
  error: string;
  generated: string;
};

/** Self-contained, print-friendly HTML for one audit entry (styled like the
 *  drawer). `piiMap` non-null renders un-masked personal data. */
export function buildAuditPdfHtml(
  entry: AiAuditLogDetailOut,
  parsedRequest: ParsedRequest | null,
  parsedResponse: ParsedResponse | null,
  piiMap: Record<string, string> | null,
  labels: PdfLabels,
  when: string,
): string {
  const block = (role: string, label: string, body: string) => {
    const color = roleColor[role] ?? roleColor.system;
    return `<div class="msg" style="border-left-color:${color}">
      <div class="role" style="color:${color}">${escapeHtml(label)}</div>
      <pre>${escapeHtml(applyPiiMap(body, piiMap)) || "<span class='empty'>—</span>"}</pre>
    </div>`;
  };
  const messages = (parsedRequest?.messages ?? [])
    .map((m) => {
      const label = m.name ? `${m.role} (${m.name})` : m.role;
      const parts = [messageContentText(m)];
      const tc = (m.tool_calls ?? [])
        .map((t) => `${t.name ?? t.function?.name ?? "?"}(${t.function?.arguments ?? ""})`)
        .join("\n");
      if (tc) parts.push(tc);
      return block(m.role, label, parts.filter(Boolean).join("\n\n"));
    })
    .join("");
  const responseBlock = parsedResponse?.content
    ? block("assistant", labels.response, parsedResponse.content)
    : "";
  const errorBlock = entry.error ? block("error", labels.error, entry.error) : "";
  const row = (k: string, v: string) =>
    `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(v)}</td></tr>`;
  const meta = [
    row(labels.fields.time, when),
    row(labels.fields.feature, entry.feature),
    row(labels.fields.provider, `${entry.provider_name || "—"} · ${entry.model || "—"}`),
    row(labels.fields.ticket, entry.ticket_id != null ? `#${entry.ticket_id}` : "—"),
    row(labels.fields.runId, entry.run_id ?? "—"),
    row(labels.fields.trigger, entry.trigger ?? "—"),
    row(labels.fields.status, String(entry.status_code ?? (entry.error ? "error" : "—"))),
    row(labels.fields.tokens, `${entry.prompt_tokens ?? 0} / ${entry.completion_tokens ?? 0}`),
    row(labels.fields.duration, `${entry.duration_ms} ms`),
  ].join("");
  const piiTag = piiMap
    ? `<span class="pii pii-on">${escapeHtml(labels.revealed)}</span>`
    : `<span class="pii pii-off">${escapeHtml(labels.masked)}</span>`;
  return `<!doctype html><html><head><meta charset="utf-8">
    <title>${escapeHtml(labels.title)} #${entry.id}</title>
    <style>
      @page { size: A4; margin: 16mm; }
      * { box-sizing: border-box; }
      body { font: 12px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; color: #111827; }
      h1 { font-size: 18px; margin: 0 0 2px; }
      .sub { color: #6b7280; margin: 0 0 12px; }
      .pii { display:inline-block; padding:1px 8px; border-radius:999px; font-size:11px; font-weight:600; }
      .pii-off { background:#e5e7eb; color:#374151; }
      .pii-on { background:#fee2e2; color:#b91c1c; }
      table.meta { border-collapse: collapse; width: 100%; margin: 6px 0 16px; }
      table.meta th, table.meta td { text-align:left; padding:3px 8px; border-bottom:1px solid #eef0f3; vertical-align:top; }
      table.meta th { color:#6b7280; font-weight:600; width:34%; }
      .msg { border:1px solid #e5e7eb; border-left-width:4px; border-radius:6px; padding:8px 10px; margin:8px 0; page-break-inside: avoid; }
      .role { font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; margin-bottom:4px; }
      pre { margin:0; white-space:pre-wrap; word-break:break-word; font:11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
      .empty { color:#9ca3af; }
      footer { margin-top:16px; color:#9ca3af; font-size:10px; border-top:1px solid #eef0f3; padding-top:6px; }
    </style></head>
    <body>
      <h1>${escapeHtml(labels.title)} #${entry.id} &nbsp; ${piiTag}</h1>
      <p class="sub">Tiqora AI audit</p>
      <table class="meta">${meta}</table>
      ${messages}${responseBlock}${errorBlock}
      <footer>${escapeHtml(labels.generated)} ${escapeHtml(when)}</footer>
    </body></html>`;
}
