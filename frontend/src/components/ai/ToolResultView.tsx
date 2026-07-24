import { useState } from "react";
import { cn } from "@/lib/cn";

/**
 * Shared renderer for AI tool-call results (ticket AI panel + admin request
 * audit). Formats JSON results as a key/value grid (flat objects), falls
 * back to syntax-coloured pretty-printed JSON for nested shapes, and
 * unescapes literal "\\n" sequences in plain-text results.
 */

/** Best-effort JSON parse of a tool result — objects/arrays render
 * structured, everything else falls back to (unescaped) text. */
function parseToolContent(content: string): unknown | null {
  try {
    const v = JSON.parse(content) as unknown;
    return typeof v === "object" && v !== null ? v : null;
  } catch {
    return null;
  }
}

/** Literal "\n" sequences from double-encoded tool output become real
 * newlines; content that already has real newlines is left untouched. */
function unescapeToolText(content: string): string {
  if (content.includes("\n") || !content.includes("\\n")) return content;
  return content
    .replace(/\\r\\n/g, "\n")
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "  ");
}

function JsonPretty({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const pad = "  ".repeat(depth + 1);
  const closePad = "  ".repeat(depth);
  if (Array.isArray(value)) {
    if (value.length === 0) return <span>[]</span>;
    return (
      <>
        {"[\n"}
        {value.map((v, i) => (
          <span key={i}>
            {pad}
            <JsonPretty value={v} depth={depth + 1} />
            {i < value.length - 1 ? "," : ""}
            {"\n"}
          </span>
        ))}
        {closePad}]
      </>
    );
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span>{"{}"}</span>;
    return (
      <>
        {"{\n"}
        {entries.map(([k, v], i) => (
          <span key={k}>
            {pad}
            <span className="text-purple">"{k}"</span>
            {": "}
            <JsonPretty value={v} depth={depth + 1} />
            {i < entries.length - 1 ? "," : ""}
            {"\n"}
          </span>
        ))}
        {closePad}
        {"}"}
      </>
    );
  }
  if (typeof value === "string")
    return <span className="text-green">"{value}"</span>;
  if (typeof value === "number" || typeof value === "boolean")
    return <span className="text-escalation">{String(value)}</span>;
  return <span className="text-muted">null</span>;
}

/** Scalar-only values render as a key/value grid (V1 look); as soon as a
 * value nests, the whole result falls back to pretty-printed JSON with
 * syntax colours (V2 look). Non-JSON content renders as unescaped text. */
export function ToolResultBody({ content }: { content: string }) {
  const parsed = parseToolContent(content);
  if (parsed === null) {
    return (
      <p className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-ink/90">
        {unescapeToolText(content)}
      </p>
    );
  }
  const entries = Array.isArray(parsed)
    ? null
    : Object.entries(parsed as Record<string, unknown>);
  const flat =
    entries !== null &&
    entries.every(
      ([, v]) =>
        v === null ||
        typeof v !== "object" ||
        (Array.isArray(v) &&
          v.every((x) => typeof x !== "object" || x === null)),
    );
  if (entries !== null && flat) {
    return (
      <dl className="grid grid-cols-[minmax(6rem,max-content)_1fr] gap-x-4 gap-y-1 overflow-x-auto text-[12px]">
        {entries.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="pt-px font-mono text-[11px] text-muted">{k}</dt>
            <dd className="m-0 min-w-0">
              {Array.isArray(v) ? (
                <span className="flex flex-wrap gap-1">
                  {v.map((item, i) => (
                    <span
                      key={i}
                      className="rounded-full bg-accent-dim px-2 py-px text-[11px] font-medium text-accent"
                    >
                      {String(item)}
                    </span>
                  ))}
                </span>
              ) : typeof v === "string" ? (
                <span className="whitespace-pre-wrap break-words text-ink">
                  {v}
                </span>
              ) : (
                <span className="font-mono text-ink">{String(v)}</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return (
    <pre className="overflow-x-auto rounded bg-surface p-2 font-mono text-[11px] leading-relaxed text-ink/90">
      <JsonPretty value={parsed} />
    </pre>
  );
}

/** One collapsible card per tool call (admin-only view). */
export function ToolTraceCard({
  name,
  content,
  testId,
}: {
  name: string;
  content: string;
  testId: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="overflow-hidden rounded-md border border-hairline">
      <button
        type="button"
        aria-expanded={open}
        data-testid={testId}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 bg-surface-subtle px-2.5 py-1.5 text-left transition-colors hover:bg-surface"
      >
        <span
          aria-hidden
          className="h-1.5 w-1.5 flex-none rounded-full bg-green"
        />
        <span className="font-mono text-[11px] font-semibold text-ink">
          {name}
        </span>
        <span
          aria-hidden
          className={cn(
            "ml-auto text-[10px] text-muted transition-transform",
            open && "rotate-90",
          )}
        >
          ▶
        </span>
      </button>
      {open && (
        <div className="px-2.5 py-2" data-testid={`${testId}-body`}>
          <ToolResultBody content={content} />
        </div>
      )}
    </div>
  );
}
