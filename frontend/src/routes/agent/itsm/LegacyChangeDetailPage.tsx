import { useQuery } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { LegacySourceBadge } from "@/components/itsm/change/LegacySourceBadge";
import { changeErrorText, SECTION_CLASS, utcText } from "@/components/itsm/change/changeUi";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { Spinner } from "@/components/ui/Spinner";
import { changeApi, changeErrorCode, changeKeys, type LegacyChange } from "@/lib/changeApi";
import { ciDetailHref, type CiSource } from "@/lib/itsmRoutes";

/*
 * Everything below is Znuny data (titles, notes, history values) and is rendered as plain text:
 * history values in particular may hold html-unescaped text, so nothing here uses innerHTML.
 */

const dash = "–";
const when = (iso: string | null | undefined) => (iso ? `${utcText(iso)} UTC` : dash);

/** `znuny:202` / `tiqora:5` → source and id. */
function parseCiRef(ref: string): { source: CiSource; id: number } | null {
  const m = /^(znuny|tiqora):(\d+)$/.exec(ref);
  return m ? { source: m[1] as CiSource, id: Number(m[2]) } : null;
}

/** `workorder_number` 0 is the change itself. */
function WorkOrderTag({ number }: { number: number }) {
  const { t } = useTranslation();
  return <span className="text-muted">{number === 0 ? t("itsm.change.legacy.ofChange") : t("itsm.change.legacy.ofWorkorder", { number })}</span>;
}

/**
 * `/agent/itsm/legacy-changes/$changeId`: one Znuny change, read only: data, times, CAB, work
 * orders, links to tickets, CIs and services, and the Znuny history. In live mode (Mode A) a link
 * opens the change in Znuny for editing; an archived change cannot be edited anywhere.
 */
export function LegacyChangeDetailPage() {
  const { t } = useTranslation();
  const { changeId: raw } = useParams({ from: "/agent/itsm/legacy-changes/$changeId" });
  const id = Number(raw);
  const valid = Number.isInteger(id) && id > 0;
  const q = useQuery({ queryKey: changeKeys.legacy(id), queryFn: ({ signal }) => changeApi.legacy(id, signal), enabled: valid, retry: false });
  const change = q.data;
  const code = q.isError ? changeErrorCode(q.error) : null;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6" data-testid="legacyChangeDetailPage">
      <RouterAnchor href="/agent/itsm/legacy-changes" className="text-xs text-accent hover:underline">
        {t("itsm.change.legacy.back")}
      </RouterAnchor>
      {!valid || q.isError ? (
        <div className="rounded-md border border-hairline bg-surface p-4 text-sm text-danger" role="alert" data-testid="legacy-detail-error" data-code={code ?? undefined}>
          {valid ? changeErrorText(q.error, t) : t("itsm.change.errors.legacy_change_not_found")}
        </div>
      ) : !change ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <Body change={change} />
      )}
    </div>
  );
}

function Body({ change: c }: { change: LegacyChange }) {
  const { t } = useTranslation();
  const live = c.source === "live";
  const facts: [string, string | null][] = [
    ["state", c.state],
    ["category", c.category],
    ["impact", c.impact],
    ["priority", c.priority],
    ["manager", c.manager_login],
    ["builder", c.builder_login],
  ];
  const times: [string, string | null][] = [
    ["requested", c.requested_time],
    ["plannedStart", c.planned_start],
    ["plannedEnd", c.planned_end],
    ["actualStart", c.actual_start],
    ["actualEnd", c.actual_end],
    ["created", c.created_at],
    ["changed", c.changed_at],
  ];
  const dynamicFields = Object.entries(c.dynamic_fields).filter(([, v]) => v.length > 0);

  return (
    <>
      <header className="min-w-0 space-y-1" data-testid="legacy-detail-header">
        <p className="flex flex-wrap items-center gap-2 font-mono text-xs text-muted">
          {c.number}
          <LegacySourceBadge source={c.source} />
        </p>
        <h1 className="break-words text-lg font-semibold text-ink">{c.title || c.number}</h1>
        {live && c.znuny_url && /^https?:\/\//i.test(c.znuny_url) && (
          <a href={c.znuny_url} target="_blank" rel="noreferrer noopener" className="inline-block text-sm text-accent hover:underline" data-testid="legacy-detail-znuny-link">
            {t("itsm.change.legacy.editInZnuny")}
          </a>
        )}
        {!live && (
          <p className="text-xs text-muted" data-testid="legacy-detail-archive-hint">
            {t("itsm.change.legacy.archiveHint")}
          </p>
        )}
      </header>

      <section className={SECTION_CLASS} data-testid="legacy-detail-facts">
        <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
          {facts.map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="text-xs text-muted">{t(`itsm.change.legacy.fields.${key}`)}</dt>
              <dd className="break-words text-ink" data-testid={`legacy-fact-${key}`}>
                {value || dash}
              </dd>
            </div>
          ))}
          {times.map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="text-xs text-muted">{t(`itsm.change.legacy.fields.${key}`)}</dt>
              <dd className="text-ink" data-testid={`legacy-time-${key}`}>
                {when(value)}
              </dd>
            </div>
          ))}
        </dl>
        {c.description && (
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("itsm.change.legacy.fields.description")}</h2>
            <p className="whitespace-pre-wrap break-words text-ink" data-testid="legacy-description">
              {c.description.trim()}
            </p>
          </div>
        )}
        {c.justification && (
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("itsm.change.legacy.fields.justification")}</h2>
            <p className="whitespace-pre-wrap break-words text-ink" data-testid="legacy-justification">
              {c.justification.trim()}
            </p>
          </div>
        )}
      </section>

      <section className={SECTION_CLASS} data-testid="legacy-detail-cab">
        <h2 className="text-sm font-semibold text-ink">{t("itsm.change.legacy.cab")}</h2>
        {c.cab.length === 0 ? (
          <p className="text-muted">{t("itsm.change.legacy.cabEmpty")}</p>
        ) : (
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {c.cab.map((m) => (
              <li key={`${m.kind}:${m.login}`} data-testid="legacy-cab-member">
                {m.login}
                {m.kind === "customer_user" && <span className="text-xs text-muted"> ({t("itsm.change.legacy.customerUser")})</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={SECTION_CLASS} data-testid="legacy-detail-workorders">
        <h2 className="text-sm font-semibold text-ink">{t("itsm.change.legacy.workorders")}</h2>
        {c.workorders.length === 0 ? (
          <p className="text-muted">{t("itsm.change.legacy.workordersEmpty")}</p>
        ) : (
          <ol className="space-y-2">
            {c.workorders.map((w) => (
              <li key={w.number} className="min-w-0 space-y-1 rounded-md border border-hairline bg-surface-subtle p-2" data-testid={`legacy-workorder-${w.number}`}>
                <p className="flex flex-wrap items-baseline gap-2">
                  <span className="text-xs text-muted">#{w.number}</span>
                  <span className="break-words font-medium text-ink">{w.title || dash}</span>
                  {w.work_order_type && <span className="text-xs text-muted">{w.work_order_type}</span>}
                  {w.state && <span className="text-xs text-muted">{w.state}</span>}
                </p>
                <p className="text-xs text-muted">
                  {w.agent_login ?? dash}
                  {" · "}
                  {t("itsm.change.legacy.planned")} {when(w.planned_start)} – {when(w.planned_end)}
                  {" · "}
                  {t("itsm.change.legacy.actual")} {when(w.actual_start)} – {when(w.actual_end)}
                  {" · "}
                  {t("itsm.change.legacy.effort", { planned: w.planned_effort, accounted: w.accounted_time })}
                </p>
                {w.instruction && (
                  <p className="whitespace-pre-wrap break-words text-xs text-ink">
                    <span className="text-muted">{t("itsm.change.legacy.instruction")}: </span>
                    {w.instruction.trim()}
                  </p>
                )}
                {w.report && (
                  <p className="whitespace-pre-wrap break-words text-xs text-ink">
                    <span className="text-muted">{t("itsm.change.legacy.report")}: </span>
                    {w.report.trim()}
                  </p>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className={SECTION_CLASS} data-testid="legacy-detail-links">
        <h2 className="text-sm font-semibold text-ink">{t("itsm.change.legacy.links")}</h2>
        <LinkGroup title={t("itsm.change.legacy.tickets")} empty={c.tickets.length === 0}>
          {c.tickets.map((l) => (
            <li key={`${l.ticket_id}:${l.workorder_number}:${l.link_type}`} className="min-w-0 break-words" data-testid={`legacy-ticket-${l.ticket_id}`}>
              <RouterAnchor href={`/agent/tickets/${l.ticket_id}`} className="text-accent hover:underline">
                {l.tn} {l.title}
              </RouterAnchor>{" "}
              <span className="text-xs text-muted">
                {l.state} · {l.link_type} · <WorkOrderTag number={l.workorder_number} />
              </span>
            </li>
          ))}
        </LinkGroup>
        <LinkGroup title={t("itsm.change.legacy.cis")} empty={c.cis.length === 0}>
          {c.cis.map((l) => {
            const ref = parseCiRef(l.ref);
            // An archived Znuny CI (`znuny:<id>`) has no page in Tiqora: it shows as text.
            const href = ref && (ref.source === "tiqora" || live) ? ciDetailHref(ref.source, ref.id) : null;
            const label = [l.number, l.name].filter(Boolean).join(" ") || l.ref;
            return (
              <li key={`${l.ref}:${l.workorder_number}:${l.link_type}`} className="min-w-0 break-words" data-testid={`legacy-ci-${l.ref}`}>
                {href ? (
                  <RouterAnchor href={href} className="text-accent hover:underline">
                    {label}
                  </RouterAnchor>
                ) : (
                  <span className="text-ink">{label}</span>
                )}{" "}
                <span className="text-xs text-muted">
                  {l.class_name ? `${l.class_name} · ` : ""}
                  {l.link_type} · <WorkOrderTag number={l.workorder_number} />
                </span>
              </li>
            );
          })}
        </LinkGroup>
        <LinkGroup title={t("itsm.change.legacy.services")} empty={c.services.length === 0}>
          {c.services.map((l) => (
            <li key={`${l.service_id}:${l.workorder_number}:${l.link_type}`} className="min-w-0 break-words" data-testid={`legacy-service-${l.service_id}`}>
              <span className="text-ink">{l.name ?? t("itsm.change.refs.service", { id: l.service_id })}</span>{" "}
              <span className="text-xs text-muted">
                {l.link_type} · <WorkOrderTag number={l.workorder_number} />
              </span>
            </li>
          ))}
        </LinkGroup>
        {c.hidden_links > 0 && (
          <p className="text-xs text-muted" data-testid="legacy-hidden-links">
            {t("itsm.change.hiddenCount", { count: c.hidden_links })}
          </p>
        )}
      </section>

      {dynamicFields.length > 0 && (
        <section className={SECTION_CLASS} data-testid="legacy-detail-dynamic-fields">
          <h2 className="text-sm font-semibold text-ink">{t("itsm.change.legacy.dynamicFields")}</h2>
          <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            {dynamicFields.map(([name, values]) => (
              <div key={name} className="min-w-0">
                <dt className="text-xs text-muted">{name}</dt>
                <dd className="break-words text-ink">{values.join(", ")}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <section className={SECTION_CLASS} data-testid="legacy-detail-history">
        <h2 className="text-sm font-semibold text-ink">{t("itsm.change.legacy.history")}</h2>
        {c.history.length === 0 ? (
          <p className="text-muted">{t("itsm.change.legacy.historyEmpty")}</p>
        ) : (
          <ul className="divide-y divide-hairline">
            {c.history.map((h, i) => {
              const oldText = h.old_display ?? h.old;
              const newText = h.new_display ?? h.new;
              return (
                <li key={i} className="min-w-0 py-1.5 text-xs" data-testid="legacy-history-entry">
                  <p className="text-muted">
                    {when(h.created_at)}
                    {" · "}
                    {h.user_login ?? dash}
                    {" · "}
                    {h.history_type}
                    {h.workorder_number > 0 && ` · ${t("itsm.change.legacy.ofWorkorder", { number: h.workorder_number })}`}
                  </p>
                  {(h.field || oldText || newText) && (
                    <p className="whitespace-pre-wrap break-words text-ink" data-testid="legacy-history-change">
                      {h.field ? `${h.field}: ` : ""}
                      {oldText ?? dash} → {newText ?? dash}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}

function LinkGroup({ title, empty, children }: { title: string; empty: boolean; children: React.ReactNode }) {
  if (empty) return null;
  return (
    <div className="space-y-1">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      <ul className="space-y-1">{children}</ul>
    </div>
  );
}
