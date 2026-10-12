import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { MultiSelectInput } from "@/components/fields/MultiSelectInput";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { reportsApi } from "@/lib/reportsApi";
import type { ScheduleRecipient } from "@/lib/reportSchedulesApi";
import { inputCls } from "../builder/builderUi";
import { isEmailLike, sendsToOthers, type RecipientsState } from "./scheduleForm";

/**
 * Who receives the report. Without the share right only the owner ("an mich") is possible;
 * external e-mail addresses need `report_external_recipients`. Stored addresses stay visible
 * (with the server's problem text) when that setting was switched off later. The UI offers no
 * role picker (no agent-readable role list); roles already on a schedule stay removable.
 */
export function RecipientsFields({
  value,
  onChange,
  ownerId,
  canShare,
  externalAllowed,
  views = [],
  error,
}: {
  value: RecipientsState;
  onChange: (next: RecipientsState) => void;
  ownerId: number;
  canShare: boolean;
  externalAllowed: boolean;
  /** The server's rendering of the stored recipients (labels and problems). */
  views?: ScheduleRecipient[];
  error?: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [draft, setDraft] = useState("");
  const [draftInvalid, setDraftInvalid] = useState(false);

  const agentsQ = useQuery({
    queryKey: ["reference", "agents"],
    queryFn: ({ signal }) => api.listReferenceAgents(signal),
    enabled: canShare,
    staleTime: 60_000,
  });
  const groupsQ = useQuery({
    queryKey: ["reports", "share-groups"],
    queryFn: ({ signal }) => reportsApi.shareGroups(signal),
    enabled: canShare,
    staleTime: 60_000,
  });
  const agentOptions = (agentsQ.data ?? []).map((a) => ({ value: String(a.id), label: a.full_name || a.login }));
  const groupOptions = (groupsQ.data ?? []).map((g) => ({ value: String(g.id), label: g.name }));
  const toIds = (next: string[]) => next.map(Number).filter(Number.isInteger);
  const problemOf = (kind: ScheduleRecipient["kind"], key: number | string) =>
    views.find((v) => v.kind === kind && (kind === "email" ? v.email === key : v.id === key))?.problem ?? null;
  const labelOf = (kind: ScheduleRecipient["kind"], key: number) =>
    views.find((v) => v.kind === kind && v.id === key)?.label ?? `#${key}`;

  const addEmail = (raw: string) => {
    const mail = raw.trim().toLowerCase().replace(/[,;]$/, "");
    if (!mail) return;
    if (!isEmailLike(mail)) {
      setDraftInvalid(true);
      return;
    }
    setDraftInvalid(false);
    setDraft("");
    if (!value.emails.includes(mail)) onChange({ ...value, emails: [...value.emails, mail] });
  };

  const showEmails = externalAllowed || value.emails.length > 0;
  const othersPresent = sendsToOthers(value, ownerId);

  if (!canShare) {
    return (
      <div className="space-y-2" data-testid="recipients-fields">
        <p className="rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink" data-testid="recipients-me">
          {t("reports.schedules.toMe")}
        </p>
        <p className="text-xs text-muted" data-testid="recipients-hint">
          {t("reports.schedules.needShareRight")}
        </p>
        {othersPresent && (
          <Button
            size="sm"
            onClick={() => onChange({ user_ids: [ownerId], group_ids: [], role_ids: [], emails: [] })}
            data-testid="recipients-only-me"
          >
            {t("reports.schedules.onlyMe")}
          </Button>
        )}
        {error && (
          <p className="text-xs text-danger" role="alert" data-testid="recipients-error">
            {t(error)}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="recipients-fields">
      <div className="space-y-1">
        <span id={`${id}-users`} className="block text-xs font-medium text-muted">
          {t("reports.schedules.users")}
        </span>
        <MultiSelectInput
          value={value.user_ids.map(String)}
          onChange={(next) => onChange({ ...value, user_ids: toIds(next) })}
          options={withMissing(agentOptions, value.user_ids, (n) => labelOf("user", n))}
          labelId={`${id}-users`}
          testId="recipients-users"
        />
        <ProblemList items={value.user_ids.map((n) => problemOf("user", n))} />
      </div>

      <div className="space-y-1">
        <span id={`${id}-groups`} className="block text-xs font-medium text-muted">
          {t("reports.schedules.groups")}
        </span>
        <MultiSelectInput
          value={value.group_ids.map(String)}
          onChange={(next) => onChange({ ...value, group_ids: toIds(next) })}
          options={withMissing(groupOptions, value.group_ids, (n) => labelOf("group", n))}
          labelId={`${id}-groups`}
          testId="recipients-groups"
        />
        <ProblemList items={value.group_ids.map((n) => problemOf("group", n))} />
      </div>

      {value.role_ids.length > 0 && (
        <div className="space-y-1">
          <span className="block text-xs font-medium text-muted">{t("reports.schedules.roles")}</span>
          <ul className="flex flex-wrap gap-1.5" data-testid="recipients-roles">
            {value.role_ids.map((r) => (
              <Chip
                key={r}
                label={labelOf("role", r)}
                removeLabel={t("reports.schedules.remove", { name: labelOf("role", r) })}
                onRemove={() => onChange({ ...value, role_ids: value.role_ids.filter((x) => x !== r) })}
              />
            ))}
          </ul>
        </div>
      )}

      {showEmails && (
        <div className="space-y-1" data-testid="recipients-emails">
          <label htmlFor={`${id}-email`} className="block text-xs font-medium text-muted">
            {t("reports.schedules.externalEmails")}
          </label>
          {value.emails.length > 0 && (
            <ul className="flex flex-wrap gap-1.5">
              {value.emails.map((m) => (
                <Chip
                  key={m}
                  label={m}
                  tone={problemOf("email", m) ? "problem" : "normal"}
                  removeLabel={t("reports.schedules.remove", { name: m })}
                  onRemove={() => onChange({ ...value, emails: value.emails.filter((x) => x !== m) })}
                />
              ))}
            </ul>
          )}
          <ProblemList items={value.emails.map((m) => problemOf("email", m))} />
          {externalAllowed && (
            <div className="flex gap-2">
              <input
                id={`${id}-email`}
                type="email"
                className={cn(inputCls, "w-full max-w-sm")}
                value={draft}
                placeholder={t("reports.schedules.emailPlaceholder")}
                aria-invalid={draftInvalid || undefined}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setDraftInvalid(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    addEmail(draft);
                  }
                }}
                onBlur={() => draft.trim() !== "" && addEmail(draft)}
                data-testid="recipients-email-input"
              />
              <Button size="md" onClick={() => addEmail(draft)} data-testid="recipients-email-add">
                {t("reports.schedules.add")}
              </Button>
            </div>
          )}
          {draftInvalid && (
            <p className="text-xs text-danger" role="alert">
              {t("reports.schedules.errors.email")}
            </p>
          )}
        </div>
      )}

      {error && (
        <p className="text-xs text-danger" role="alert" data-testid="recipients-error">
          {t(error)}
        </p>
      )}
    </div>
  );
}

/** Stored ids the picker's list no longer offers still need a chip. */
function withMissing(options: { value: string; label: string }[], ids: number[], label: (n: number) => string) {
  const known = new Set(options.map((o) => o.value));
  return [...options, ...ids.filter((n) => !known.has(String(n))).map((n) => ({ value: String(n), label: label(n) }))];
}

function ProblemList({ items }: { items: (string | null)[] }) {
  const problems = [...new Set(items.filter((p): p is string => p != null))];
  if (problems.length === 0) return null;
  return (
    <ul className="space-y-0.5 text-xs text-danger" data-testid="recipients-problems">
      {problems.map((p) => (
        <li key={p}>{p}</li>
      ))}
    </ul>
  );
}

function Chip({
  label,
  onRemove,
  removeLabel,
  tone = "normal",
}: {
  label: string;
  onRemove: () => void;
  removeLabel: string;
  tone?: "normal" | "problem";
}) {
  return (
    <li
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
        tone === "problem" ? "border-danger/40 bg-danger/10 text-ink" : "border-hairline bg-surface-subtle text-ink",
      )}
    >
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={removeLabel}
        className="rounded-full px-1 text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      >
        ×
      </button>
    </li>
  );
}
