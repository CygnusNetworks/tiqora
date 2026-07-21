import { useId, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  OTRS_PLACEHOLDERS,
  VARIABLE_GROUP_NOTES,
  VARIABLE_GROUP_ORDER,
  type VariableGroup,
  type VariablePlaceholder,
} from "./otrsPlaceholders";

export type VariableReferenceProps = {
  /** Called with the full placeholder tag when the user clicks a variable. */
  onInsert: (tag: string) => void;
  /** Start expanded (default collapsed so the form stays uncluttered). */
  defaultOpen?: boolean;
  className?: string;
};

type DisplayItem = {
  tag: string;
  group: VariableGroup;
  /** Already-resolved description text (i18n or plain label). */
  description: string;
};

/**
 * Compact collapsible reference of supported OTRS placeholders.
 * Static catalogue plus configured queue variables / customer fields (admin APIs).
 * Click a tag to insert it into the associated body/text field (via onInsert).
 */
export function VariableReference({
  onInsert,
  defaultOpen = false,
  className,
}: VariableReferenceProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();

  const queueVarsQ = useQuery({
    queryKey: ["admin", "queue-variables", "picker"],
    queryFn: ({ signal }) => api.adminQueueVariables.list({ pageSize: 500 }, signal),
    staleTime: 5 * 60 * 1000,
    retry: false,
    enabled: open,
  });

  const customerFieldsQ = useQuery({
    queryKey: ["admin", "customer-fields", "picker"],
    queryFn: ({ signal }) => api.adminCustomerFields.list({ pageSize: 500 }, signal),
    staleTime: 5 * 60 * 1000,
    retry: false,
    enabled: open,
  });

  const byGroup = useMemo(() => {
    const staticItems: DisplayItem[] = OTRS_PLACEHOLDERS.map((p: VariablePlaceholder) => ({
      tag: p.tag,
      group: p.group,
      description: t(p.descriptionKey),
    }));

    const seen = new Set(staticItems.map((i) => i.tag));
    const extras: DisplayItem[] = [];

    // Distinct configured queue variable names → <OTRS_QUEUE_${name}>
    const names = new Set<string>();
    for (const row of queueVarsQ.data?.items ?? []) {
      if (row.name?.trim()) names.add(row.name.trim());
    }
    for (const name of [...names].sort((a, b) => a.localeCompare(b))) {
      const tag = `<OTRS_QUEUE_${name}>`;
      if (seen.has(tag)) continue;
      seen.add(tag);
      extras.push({
        tag,
        group: "queue",
        description: t("admin.variables.items.configuredQueueVar", { name }),
      });
    }

    // Enabled customer-field registry rows → <OTRS_CUSTOMER_DATA_${tag_name}>
    for (const row of customerFieldsQ.data?.items ?? []) {
      if (!row.enabled || !row.tag_name?.trim()) continue;
      const tag = `<OTRS_CUSTOMER_DATA_${row.tag_name.trim()}>`;
      if (seen.has(tag)) continue;
      seen.add(tag);
      extras.push({
        tag,
        group: "customer",
        description: row.label?.trim() || row.column_name || row.tag_name,
      });
    }

    const all = [...staticItems, ...extras];
    return VARIABLE_GROUP_ORDER.map((group) => ({
      group,
      items: all.filter((p) => p.group === group),
    })).filter((g) => g.items.length > 0);
  }, [t, queueVarsQ.data, customerFieldsQ.data]);

  return (
    <div
      className={cn("mt-1.5 rounded-md border border-hairline bg-surface-subtle/60", className)}
      data-testid="variable-reference"
    >
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-xs font-medium text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        aria-expanded={open}
        aria-controls={panelId}
        data-testid="variable-reference-toggle"
        onClick={() => setOpen((v) => !v)}
      >
        <span>{t("admin.variables.title")}</span>
        <span className="text-[10px] uppercase tracking-wide" aria-hidden="true">
          {open ? "▲" : "▼"}
        </span>
      </button>

      {open && (
        <div
          id={panelId}
          className="border-t border-hairline px-2.5 pb-2.5 pt-2"
          data-testid="variable-reference-panel"
        >
          <p className="mb-2 text-[11px] text-muted">{t("admin.variables.insertHint")}</p>
          <div className="flex flex-col gap-2.5">
            {byGroup.map(({ group, items }) => (
              <section key={group} data-testid={`variable-reference-group-${group}`}>
                <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  {t(`admin.variables.groups.${group}`)}
                </h4>
                <ul className="flex flex-col gap-0.5">
                  {items.map((item) => (
                    <li key={item.tag}>
                      <button
                        type="button"
                        className="group flex w-full items-start gap-2 rounded px-1.5 py-1 text-left hover:bg-surface focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                        data-testid="variable-reference-tag"
                        data-tag={item.tag}
                        onClick={() => onInsert(item.tag)}
                        title={t("admin.variables.insertHint")}
                      >
                        <code className="shrink-0 rounded bg-surface px-1 py-0.5 font-mono text-[11px] text-accent group-hover:bg-surface-subtle">
                          {item.tag}
                        </code>
                        <span className="pt-0.5 text-[11px] leading-snug text-muted">
                          {item.description}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                {VARIABLE_GROUP_NOTES[group] && (
                  <p className="mt-1 px-1.5 text-[10px] leading-snug text-muted/90">
                    {t(VARIABLE_GROUP_NOTES[group]!)}
                  </p>
                )}
              </section>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
