import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";

/** One addressee: display name (may be empty) plus the email address. */
export type Recipient = { name: string; email: string };

/** A field a chip can be moved to (e.g. To → Cc). */
export type MoveTarget = { key: string; label: string };

const DND_MIME = "application/x-tiqora-recipient";

/**
 * Parse a raw recipient string into {name, email}. Accepts the common mail
 * forms "Name <email@x>" and a bare "email@x"; surrounding quotes on the name
 * are stripped. Returns null when no plausible address is present so callers
 * can ignore stray input.
 */
export function parseRecipient(raw: string): Recipient | null {
  const value = raw.trim();
  if (!value) return null;
  const angled = value.match(/^\s*(.*?)\s*<\s*([^>]+?)\s*>\s*$/);
  if (angled) {
    const email = angled[2].trim();
    if (!email.includes("@")) return null;
    return { name: angled[1].replace(/^["']|["']$/g, "").trim(), email };
  }
  if (!value.includes("@") || /\s/.test(value)) return null;
  return { name: "", email: value };
}

/** Render a recipient back to "Name <email>" (or bare email when unnamed). */
export function formatRecipient(r: Recipient): string {
  return r.name ? `${r.name} <${r.email}>` : r.email;
}

/**
 * Split a comma-joined address header (as stored by Znuny) into recipients,
 * ignoring commas inside a "<...>" bracket or a quoted display name.
 * Unparseable fragments are dropped.
 */
export function parseRecipientList(raw: string | null | undefined): Recipient[] {
  if (!raw) return [];
  const out: Recipient[] = [];
  let buf = "";
  let inAngle = false;
  let inQuote = false;
  for (const ch of raw) {
    if (ch === '"') inQuote = !inQuote;
    else if (ch === "<") inAngle = true;
    else if (ch === ">") inAngle = false;
    if (ch === "," && !inAngle && !inQuote) {
      const r = parseRecipient(buf);
      if (r) out.push(r);
      buf = "";
    } else {
      buf += ch;
    }
  }
  const last = parseRecipient(buf);
  if (last) out.push(last);
  return out;
}

/** Join recipients back into a comma-separated header string (or null). */
export function joinRecipients(recipients: Recipient[]): string | null {
  return recipients.length ? recipients.map(formatRecipient).join(", ") : null;
}

const chipCls =
  "group inline-flex max-w-full items-center gap-1 rounded-full border border-hairline " +
  "bg-surface-subtle py-0.5 pl-2 pr-1 text-xs text-ink";

const inputCls =
  "min-w-[8rem] flex-1 bg-transparent text-sm text-ink placeholder:text-muted focus:outline-none";

/**
 * Apple-Mail-style recipient editor: each address is a draggable chip showing
 * the display name (falling back to the email). Clicking a chip opens an inline
 * editor for name + email; chips can be dragged between fields or moved with the
 * explicit action buttons (the non-drag fallback). New addresses are added by
 * typing "Name <email>" or a bare address and pressing Enter.
 */
export function RecipientsField({
  label,
  fieldKey,
  recipients,
  onChange,
  onMove,
  moveTargets = [],
  placeholder,
  testid,
}: {
  label: string;
  fieldKey: string;
  recipients: Recipient[];
  onChange: (next: Recipient[]) => void;
  /** Cross-field move: parent removes from `from` and appends to `to`. */
  onMove?: (from: string, to: string, recipient: Recipient) => void;
  moveTargets?: MoveTarget[];
  placeholder?: string;
  testid?: string;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const commitDraft = () => {
    const parsed = parseRecipient(draft);
    if (!parsed) return;
    onChange([...recipients, parsed]);
    setDraft("");
  };

  const removeAt = (i: number) => {
    setEditing(null);
    onChange(recipients.filter((_, idx) => idx !== i));
  };

  const editAt = (i: number, patch: Partial<Recipient>) =>
    onChange(recipients.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const raw = e.dataTransfer.getData(DND_MIME);
    if (!raw) return;
    try {
      const { from, recipient } = JSON.parse(raw) as {
        from: string;
        recipient: Recipient;
      };
      if (from !== fieldKey) onMove?.(from, fieldKey, recipient);
    } catch {
      /* ignore malformed drops */
    }
  };

  return (
    <div className="block text-xs text-muted">
      <span className="mb-1 block">{label}</span>
      <div
        data-testid={testid}
        onClick={() => inputRef.current?.focus()}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes(DND_MIME)) {
            e.preventDefault();
            setDragOver(true);
          }
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={cn(
          "flex min-h-[2.25rem] flex-wrap items-center gap-1 rounded border bg-surface px-1.5 py-1",
          "focus-within:ring-1 focus-within:ring-accent",
          dragOver ? "border-accent ring-1 ring-accent" : "border-hairline",
        )}
      >
        {recipients.map((r, i) => (
          <span key={`${r.email}-${i}`} className="relative">
            <span
              className={cn(chipCls, editing === i && "ring-1 ring-accent")}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData(
                  DND_MIME,
                  JSON.stringify({ from: fieldKey, recipient: r }),
                );
                e.dataTransfer.effectAllowed = "move";
              }}
            >
              <button
                type="button"
                className="cursor-grab truncate text-left"
                title={formatRecipient(r)}
                data-testid={testid ? `${testid}-chip` : undefined}
                onClick={(e) => {
                  e.stopPropagation();
                  setEditing(editing === i ? null : i);
                }}
              >
                {r.name || r.email}
              </button>
              <button
                type="button"
                aria-label={t("ticket.recipientRemove")}
                className="rounded-full px-1 text-muted hover:bg-danger/15 hover:text-danger"
                data-testid={testid ? `${testid}-remove` : undefined}
                onClick={(e) => {
                  e.stopPropagation();
                  removeAt(i);
                }}
              >
                ×
              </button>
            </span>
            {editing === i && (
              <div
                className="absolute left-0 top-full z-20 mt-1 w-64 space-y-2 rounded-lg border border-hairline bg-surface p-2 shadow-xl"
                onClick={(e) => e.stopPropagation()}
              >
                <label className="block text-[11px] text-muted">
                  {t("ticket.recipientName")}
                  <input
                    autoFocus
                    className="mt-0.5 w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent"
                    value={r.name}
                    data-testid={testid ? `${testid}-name` : undefined}
                    onChange={(e) => editAt(i, { name: e.target.value })}
                  />
                </label>
                <label className="block text-[11px] text-muted">
                  {t("ticket.recipientEmail")}
                  <input
                    className="mt-0.5 w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent"
                    value={r.email}
                    data-testid={testid ? `${testid}-email` : undefined}
                    onChange={(e) => editAt(i, { email: e.target.value })}
                  />
                </label>
                <div className="flex flex-wrap items-center justify-between gap-1 pt-0.5">
                  <div className="flex flex-wrap gap-1">
                    {moveTargets.map((mt) => (
                      <button
                        key={mt.key}
                        type="button"
                        className="rounded border border-hairline px-1.5 py-0.5 text-[11px] text-muted hover:text-ink"
                        data-testid={testid ? `${testid}-move-${mt.key}` : undefined}
                        onClick={() => {
                          setEditing(null);
                          onMove?.(fieldKey, mt.key, r);
                        }}
                      >
                        → {mt.label}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="rounded px-1.5 py-0.5 text-[11px] text-danger hover:bg-danger/15"
                    onClick={() => removeAt(i)}
                  >
                    {t("ticket.recipientRemove")}
                  </button>
                </div>
              </div>
            )}
          </span>
        ))}
        <input
          ref={inputRef}
          className={inputCls}
          value={draft}
          placeholder={recipients.length === 0 ? placeholder : undefined}
          data-testid={testid ? `${testid}-input` : undefined}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              commitDraft();
            } else if (e.key === "Backspace" && !draft && recipients.length) {
              removeAt(recipients.length - 1);
            }
          }}
          onBlur={commitDraft}
        />
      </div>
    </div>
  );
}
