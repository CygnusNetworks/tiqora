import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { AgentRef } from "@/lib/api";
import { cn } from "@/lib/cn";
import { mentionQueryAt, type PickedMention } from "@/lib/mentions";

/** How many suggestions the panel shows at once. */
const MAX_SUGGESTIONS = 6;

/**
 * The reply/note body with an `@` typeahead over the agent list — the same
 * gesture as Slack, Teams and GitHub, replacing the separate "Agent erwähnen"
 * dropdown that used to sit in a panel of its own.
 *
 * Picking inserts `@Full Name` into the text and records the id. Deleting the
 * name from the body drops the mention again (see `survivingMentions`), so the
 * text stays the single source of truth.
 */
export function MentionTextarea({
  value,
  onChange,
  mentions,
  onMentionsChange,
  rows = 6,
  placeholder,
  className,
  testId,
  textareaRef,
  ariaLabel,
  readOnly = false,
}: {
  value: string;
  onChange: (value: string) => void;
  mentions: PickedMention[];
  onMentionsChange: (mentions: PickedMention[]) => void;
  rows?: number;
  placeholder?: string;
  className?: string;
  testId?: string;
  textareaRef?: React.RefObject<HTMLTextAreaElement | null>;
  ariaLabel?: string;
  /** Locks the text once it has been sent (see the composers' retry state). */
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const localRef = useRef<HTMLTextAreaElement | null>(null);
  const ref = textareaRef ?? localRef;
  const [token, setToken] = useState<{ start: number; query: string } | null>(null);
  const [active, setActive] = useState(0);
  /** Caret position to restore after an insertion (state changes are async). */
  const caretAfterInsert = useRef<number | null>(null);

  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: () => api.listReferenceAgents(),
    // Only worth fetching once the agent actually reaches for the feature.
    enabled: token !== null,
  });

  const suggestions = useMemo(() => {
    if (!token) return [];
    const needle = token.query.toLowerCase();
    return (agentsQ.data ?? [])
      .filter(
        (a: AgentRef) =>
          !needle ||
          a.full_name.toLowerCase().includes(needle) ||
          a.login.toLowerCase().includes(needle),
      )
      .slice(0, MAX_SUGGESTIONS);
  }, [agentsQ.data, token]);

  useEffect(() => setActive(0), [token?.query]);

  useEffect(() => {
    const caret = caretAfterInsert.current;
    if (caret == null) return;
    caretAfterInsert.current = null;
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(caret, caret);
  }, [value, ref]);

  const syncToken = (el: HTMLTextAreaElement) => {
    setToken(mentionQueryAt(el.value, el.selectionStart ?? el.value.length));
  };

  const insert = (agent: AgentRef) => {
    if (!token) return;
    const el = ref.current;
    const caret = el?.selectionStart ?? value.length;
    const inserted = `@${agent.full_name} `;
    const next = value.slice(0, token.start) + inserted + value.slice(caret);
    caretAfterInsert.current = token.start + inserted.length;
    onChange(next);
    if (!mentions.some((m) => m.id === agent.id)) {
      onMentionsChange([...mentions, { id: agent.id, name: agent.full_name }]);
    }
    setToken(null);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!token || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + suggestions.length) % suggestions.length);
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      insert(suggestions[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setToken(null);
    }
  };

  const open = !readOnly && token !== null && suggestions.length > 0;

  return (
    <div className="relative">
      <textarea
        ref={ref}
        value={value}
        rows={rows}
        placeholder={placeholder}
        data-testid={testId}
        aria-label={ariaLabel}
        readOnly={readOnly}
        role="combobox"
        aria-expanded={open}
        aria-controls={open ? "mention-typeahead" : undefined}
        aria-autocomplete="list"
        className={cn(
          "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent",
          className,
        )}
        onChange={(e) => {
          onChange(e.target.value);
          syncToken(e.target);
        }}
        onKeyUp={(e) => syncToken(e.currentTarget)}
        onClick={(e) => syncToken(e.currentTarget)}
        onKeyDown={onKeyDown}
        onBlur={() => setToken(null)}
      />
      {open && (
        <ul
          id="mention-typeahead"
          role="listbox"
          aria-label={t("ticket.mentionSuggestions")}
          data-testid="mention-typeahead"
          className="absolute left-2 top-full z-20 mt-1 max-h-52 w-64 overflow-y-auto rounded-xl border border-hairline bg-surface p-1 shadow-xl"
        >
          {suggestions.map((a, i) => (
            <li key={a.id}>
              <button
                type="button"
                role="option"
                aria-selected={i === active}
                data-testid={`mention-option-${a.id}`}
                // Runs before the textarea's blur would close the panel.
                onMouseDown={(e) => {
                  e.preventDefault();
                  insert(a);
                }}
                onMouseEnter={() => setActive(i)}
                className={cn(
                  "flex w-full items-baseline gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px]",
                  i === active ? "bg-surface-subtle text-ink" : "text-ink/90",
                )}
              >
                <span className="min-w-0 flex-1 truncate">{a.full_name}</span>
                <span className="shrink-0 font-mono text-[11px] text-muted">{a.login}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
