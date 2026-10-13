import { useEffect, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CiStateBadge } from "@/components/itsm/CiStateBadge";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { itsmApi, itsmErrorKey, itsmKeys, linkTypeLabel, type CiSummary } from "@/lib/itsmApi";

const PAGE = 20;
const DEBOUNCE_MS = 250;

/**
 * "CI verknüpfen": search (debounced) with class filter and link-type select; arrow keys move
 * through the hits, Enter or a click picks one. The picker only chooses — `onPick` links.
 */
export function CiPicker({
  onClose,
  onPick,
  linked,
  busy = false,
  error = null,
}: {
  onClose: () => void;
  /** `linkType` is `null` for "the configured default". */
  onPick: (ci: CiSummary, linkType: string | null) => void;
  /** `ref` values (`"znuny:12"`) already linked: shown but not selectable. */
  linked: ReadonlySet<string>;
  busy?: boolean;
  error?: string | null;
}) {
  const { t } = useTranslation();
  const listId = useId();
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [classKey, setClassKey] = useState("");
  const [linkType, setLinkType] = useState("");
  const [active, setActive] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setQ(text.trim()), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text]);

  const catalogQ = useQuery({
    queryKey: itsmKeys.catalog(),
    queryFn: ({ signal }) => itsmApi.catalog(signal),
    retry: false,
    staleTime: 60_000,
  });
  const typesQ = useQuery({
    queryKey: itsmKeys.linkTypes("ticket_ci"),
    queryFn: ({ signal }) => itsmApi.linkTypes("ticket_ci", signal),
    retry: false,
  });
  const query = { q: q || undefined, class: classKey ? [classKey] : undefined, limit: PAGE };
  const hitsQ = useQuery({
    queryKey: itsmKeys.cis(query),
    queryFn: ({ signal }) => itsmApi.cis(query, signal),
    retry: false,
    placeholderData: (prev) => prev,
  });
  const hits = hitsQ.data?.items ?? [];
  const current = Math.min(active, Math.max(hits.length - 1, 0));

  const classItems = [
    { value: "", label: t("itsm.zoom.picker.allClasses") },
    ...(catalogQ.data?.classes ?? []).map((c) => ({ value: c.key, label: c.name })),
  ];
  const typeItems = [
    { value: "", label: t("itsm.zoom.picker.defaultLinkType") },
    ...(typesQ.data ?? []).map((o) => ({ value: o.name, label: linkTypeLabel(o) })),
  ];

  const pick = (ci: CiSummary | undefined) => {
    if (!ci || busy || linked.has(ci.ref)) return;
    onPick(ci, linkType || null);
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(Math.min(current + 1, hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(Math.max(current - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(hits[current]);
    }
  };

  return (
    <Dialog open onClose={onClose} title={t("itsm.zoom.picker.title")} size="xl">
      <div className="space-y-3" data-testid="ci-picker">
        <input
          autoFocus
          role="combobox"
          aria-expanded
          aria-controls={listId}
          aria-activedescendant={hits[current] ? `${listId}-${hits[current].id}` : undefined}
          aria-label={t("itsm.zoom.picker.search")}
          placeholder={t("itsm.zoom.picker.search")}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink focus:border-accent"
          data-testid="ci-picker-search"
        />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="block text-xs text-muted">
            {t("itsm.zoom.picker.class")}
            <SelectField
              items={classItems}
              value={classKey}
              onChange={(v) => {
                setClassKey(v);
                setActive(0);
              }}
              testId="ci-picker-class"
              className="mt-1"
            />
          </label>
          <label className="block text-xs text-muted">
            {t("itsm.zoom.picker.linkType")}
            <SelectField items={typeItems} value={linkType} onChange={setLinkType} testId="ci-picker-link-type" className="mt-1" />
          </label>
        </div>
        {hitsQ.isError ? (
          <p className="text-sm text-muted" role="alert" data-testid="ci-picker-unavailable">
            {t(itsmErrorKey(hitsQ.error))}
          </p>
        ) : hitsQ.isLoading ? (
          <div className="flex justify-center py-4">
            <Spinner />
          </div>
        ) : hits.length === 0 ? (
          <p className="text-sm text-muted" data-testid="ci-picker-empty">
            {t("itsm.ci.empty")}
          </p>
        ) : (
          <ul id={listId} role="listbox" className="max-h-72 divide-y divide-hairline overflow-auto rounded border border-hairline">
            {hits.map((ci, i) => {
              const isLinked = linked.has(ci.ref);
              return (
                <li
                  key={ci.ref}
                  id={`${listId}-${ci.id}`}
                  role="option"
                  aria-selected={i === current}
                  aria-disabled={isLinked || undefined}
                  data-testid={`ci-picker-hit-${ci.id}`}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => pick(ci)}
                  className={`flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-sm ${
                    i === current ? "bg-accent-dim" : ""
                  } ${isLinked ? "cursor-not-allowed opacity-60" : ""}`}
                >
                  <span className="font-mono text-xs text-accent">{ci.number}</span>
                  <span className="min-w-0 flex-1 truncate text-ink">{ci.name}</span>
                  <span className="text-xs text-muted">{ci.class_name}</span>
                  <CiStateBadge state={ci.incident_state} />
                  {isLinked && <span className="text-xs text-muted">{t("itsm.zoom.picker.alreadyLinked")}</span>}
                </li>
              );
            })}
          </ul>
        )}
        {hitsQ.data?.truncated && (
          <p className="text-xs text-muted" data-testid="ci-picker-truncated">
            {t("itsm.zoom.picker.truncated")}
          </p>
        )}
        {error && (
          <p className="text-xs text-danger" role="alert" data-testid="ci-picker-error">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
