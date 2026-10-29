import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Snapshot } from "@/components/itsm/change/ChangeApprovalSection";
import { ChangeTypeBadge } from "@/components/itsm/change/ChangeTypeBadge";
import { type AgendaDraftItem, MEETING_STATUS_TONE, moveItem, visibleAgenda } from "@/components/itsm/change/cabUi";
import { changeErrorText, FIELD_CLASS, LABEL_CLASS, PHASE_TONE, SECTION_CLASS, utcText, windowText } from "@/components/itsm/change/changeUi";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { changeApi, changeErrorCode, changeErrorDetail, changeKeys, type CabChangeItem, type CabMeeting } from "@/lib/changeApi";
import { changeDetailHref } from "@/lib/itsmRoutes";

const STATUSES: CabMeeting["status"][] = ["planned", "held", "cancelled"];

/**
 * `/agent/itsm/cab-meetings/$meetingId`: one CAB meeting. The agenda lists the changes with their
 * approval status (the votes themselves are cast on the change); change managers order it (drag or
 * the arrows), add changes from the suggestions (changes waiting for this CAB), keep the minutes and
 * set the status. Agenda lines the caller may not read stay on the agenda as "Change ohne Leserecht".
 */
export function CabMeetingPage() {
  const { t } = useTranslation();
  const { meetingId: raw } = useParams({ from: "/agent/itsm/cab-meetings/$meetingId" });
  const id = Number(raw);
  const valid = Number.isInteger(id) && id > 0;
  const statusQ = useQuery({ queryKey: changeKeys.status(), queryFn: ({ signal }) => changeApi.status(signal), retry: false });
  const q = useQuery({ queryKey: changeKeys.meeting(id), queryFn: ({ signal }) => changeApi.meeting(id, signal), enabled: valid, retry: false });
  const meeting = q.data;
  const canManage = !!statusQ.data?.can_manage;
  const code = q.isError ? changeErrorCode(q.error) : null;

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-6" data-testid="cabMeetingPage">
      <RouterAnchor href="/agent/itsm/cab-meetings" className="text-xs text-accent hover:underline">
        {t("itsm.change.cab.back")}
      </RouterAnchor>
      {!valid || q.isError ? (
        <div className="rounded-md border border-hairline bg-surface p-4 text-sm text-danger" role="alert" data-testid="cab-meeting-error" data-code={code ?? undefined}>
          {valid ? changeErrorText(q.error, t) : t("itsm.change.errors.meeting_not_found")}
        </div>
      ) : !meeting ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <MeetingBody meeting={meeting} canManage={canManage} />
      )}
    </div>
  );
}

function MeetingBody({ meeting, canManage }: { meeting: CabMeeting; canManage: boolean }) {
  const { t } = useTranslation();
  return (
    <>
      <header className="min-w-0 space-y-1" data-testid="cab-meeting-header">
        <h1 className="break-words text-lg font-semibold text-ink">{meeting.title}</h1>
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <Badge tone={MEETING_STATUS_TONE[meeting.status]} data-testid="cab-meeting-status" data-status={meeting.status}>
            {t(`itsm.change.cab.status.${meeting.status}`)}
          </Badge>
          <span>{meeting.cab_name}</span>
          <span>
            {utcText(meeting.starts_at)} – {utcText(meeting.ends_at).slice(11)} UTC
          </span>
          {meeting.location && <span>{meeting.location}</span>}
        </p>
      </header>
      <AgendaSection meeting={meeting} canEdit={canManage && meeting.status === "planned"} />
      <MinutesSection meeting={meeting} canManage={canManage} />
    </>
  );
}

function AgendaSection({ meeting, canEdit }: { meeting: CabMeeting; canEdit: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [draft, setDraft] = useState<AgendaDraftItem[] | null>(null);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  // After a move: which row and arrow keeps the focus, and what the live region announces.
  const [moved, setMoved] = useState<{ id: number; btn: "up" | "down"; pos: number; title: string } | null>(null);
  const items = draft ?? visibleAgenda(meeting);
  const save = useMutation({
    mutationFn: () => changeApi.saveAgenda(meeting.id, { items: items.map((i) => ({ change_ticket_id: i.ticket_id, note: i.note.trim() || null })) }),
    onSuccess: (saved) => {
      qc.setQueryData(changeKeys.meeting(meeting.id), saved);
      void qc.invalidateQueries({ queryKey: [...changeKeys.all(), "meetings"] });
      setDraft(null);
    },
  });
  const hidden = meeting.agenda.filter((e) => e.hidden);
  const edit = (next: AgendaDraftItem[]) => {
    save.reset();
    setDraft(next);
  };
  const move = (from: number, to: number, btn: "up" | "down") => {
    edit(moveItem(items, from, to));
    setMoved({ id: items[from].ticket_id, btn, pos: to + 1, title: items[from].title });
  };
  useEffect(() => {
    if (!moved) return;
    // A button that became disabled (end of the list) loses focus: use the other arrow, else the row.
    const get = (b: string) => document.querySelector<HTMLButtonElement>(`[data-testid="cab-agenda-${b}-${moved.id}"]`);
    const target = [get(moved.btn), get(moved.btn === "up" ? "down" : "up")].find((b) => b && !b.disabled);
    target?.focus();
  }, [moved, draft]);

  // `change_not_open` / `change_not_found` name the blocking line.
  const blockedRaw = save.isError ? changeErrorDetail(save.error)?.ticket_id : undefined;
  const blockedId = typeof blockedRaw === "number" ? blockedRaw : null;
  const blocked = items.find((i) => i.ticket_id === blockedId);

  const suggestionsQ = useQuery({
    queryKey: changeKeys.meetingSuggestions(meeting.id),
    queryFn: ({ signal }) => changeApi.meetingSuggestions(meeting.id, signal),
    enabled: canEdit,
    retry: false,
  });
  const onAgenda = new Set(items.map((i) => i.ticket_id));
  const suggestions = (suggestionsQ.data ?? []).filter((s) => !onAgenda.has(s.ticket_id));


  const add = (s: CabChangeItem) =>
    edit([
      ...items,
      { ticket_id: s.ticket_id, tn: s.tn, title: s.title, change_type: s.change_type, phase: s.phase, planned_start: s.planned_start, planned_end: s.planned_end, note: "", approval: null, unsaved: true },
    ]);

  return (
    <section className={SECTION_CLASS} data-testid="cab-agenda">
      <h2 className="text-sm font-semibold text-ink">{t("itsm.change.cab.agenda")}</h2>
      {hidden.length > 0 && (
        <p className="text-xs text-muted" data-testid="cab-agenda-hidden-hint">
          {t("itsm.change.cab.hiddenHint")}
        </p>
      )}
      <p className="sr-only" role="status" aria-live="polite" data-testid="cab-agenda-live">
        {moved ? t("itsm.change.cab.position", { pos: moved.pos, count: items.length, title: moved.title }) : ""}
      </p>
      {items.length === 0 && hidden.length === 0 ? (
        <p className="text-sm text-muted" data-testid="cab-agenda-empty">
          {t("itsm.change.cab.agendaEmpty")}
        </p>
      ) : (
        <ol className="space-y-2" data-testid="cab-agenda-list">
          {items.map((item, index) => (
            <li
              key={item.ticket_id}
              className={`min-w-0 rounded-md border bg-surface-subtle p-2 ${blockedId === item.ticket_id ? "border-danger" : dragOver === index && dragFrom !== null && dragFrom !== index ? "border-accent" : "border-hairline"}`}
              data-testid={`cab-agenda-item-${item.ticket_id}`}
              data-blocked={blockedId === item.ticket_id || undefined}
              onDragOver={(e) => {
                if (!canEdit || dragFrom === null) return;
                e.preventDefault();
                setDragOver(index);
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (dragFrom !== null && dragFrom !== index) move(dragFrom, index, dragFrom < index ? "down" : "up");
                setDragFrom(null);
                setDragOver(null);
              }}
            >
              <div className="flex min-w-0 items-start gap-2">
                {canEdit && (
                  <button
                    type="button"
                    draggable
                    className="mt-0.5 shrink-0 cursor-grab text-muted hover:text-ink"
                    aria-label={t("itsm.change.cab.dragHandle", { title: item.title })}
                    data-testid={`cab-agenda-handle-${item.ticket_id}`}
                    onDragStart={(e) => {
                      setDragFrom(index);
                      e.dataTransfer.effectAllowed = "move";
                      e.dataTransfer.setData("text/plain", String(item.ticket_id));
                      const row = e.currentTarget.closest("li");
                      if (row && e.dataTransfer.setDragImage) e.dataTransfer.setDragImage(row, 0, 0);
                    }}
                    onDragEnd={() => {
                      setDragFrom(null);
                      setDragOver(null);
                    }}
                  >
                    ⠿
                  </button>
                )}
                <span className="mt-0.5 w-5 shrink-0 text-right text-xs text-muted">{index + 1}.</span>
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <RouterAnchor href={changeDetailHref(item.ticket_id)} className="min-w-0 break-words text-sm font-medium text-accent hover:underline" data-testid={`cab-agenda-link-${item.ticket_id}`}>
                      {item.title}
                    </RouterAnchor>
                    {item.change_type && <ChangeTypeBadge type={item.change_type} />}
                    {item.phase && <Badge tone={PHASE_TONE[item.phase]}>{t(`itsm.change.phases.${item.phase}`)}</Badge>}
                  </div>
                  <p className="text-xs text-muted">
                    <span className="font-mono">{item.tn}</span>
                    {windowText(item.planned_start, item.planned_end) && ` · ${windowText(item.planned_start, item.planned_end)} UTC`}
                  </p>
                  {item.approval ? (
                    <Snapshot snapshot={item.approval} />
                  ) : (
                    <p className="text-xs text-muted" data-testid={item.unsaved ? "cab-agenda-unsaved" : "cab-agenda-no-approval"}>
                      {t(item.unsaved ? "itsm.change.cab.unsaved" : "itsm.change.cab.noApproval")}
                    </p>
                  )}
                  {canEdit ? (
                    <input
                      className={FIELD_CLASS}
                      aria-label={t("itsm.change.cab.note")}
                      placeholder={t("itsm.change.cab.note")}
                      maxLength={5000}
                      value={item.note}
                      data-testid={`cab-agenda-note-${item.ticket_id}`}
                      onChange={(e) => edit(items.map((i) => (i.ticket_id === item.ticket_id ? { ...i, note: e.target.value } : i)))}
                    />
                  ) : (
                    item.note && (
                      <p className="whitespace-pre-wrap break-words text-xs text-ink" data-testid={`cab-agenda-note-text-${item.ticket_id}`}>
                        {item.note}
                      </p>
                    )
                  )}
                </div>
                {canEdit && (
                  <div className="flex shrink-0 flex-col gap-1 sm:flex-row">
                    <Button size="sm" variant="ghost" disabled={index === 0} onClick={() => move(index, index - 1, "up")} aria-label={t("itsm.change.cab.moveUp")} data-testid={`cab-agenda-up-${item.ticket_id}`}>
                      ↑
                    </Button>
                    <Button size="sm" variant="ghost" disabled={index === items.length - 1} onClick={() => move(index, index + 1, "down")} aria-label={t("itsm.change.cab.moveDown")} data-testid={`cab-agenda-down-${item.ticket_id}`}>
                      ↓
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => edit(items.filter((i) => i.ticket_id !== item.ticket_id))} aria-label={t("itsm.change.cab.remove")} data-testid={`cab-agenda-remove-${item.ticket_id}`}>
                      ✕
                    </Button>
                  </div>
                )}
              </div>
            </li>
          ))}
          {hidden.map((e) => (
            <li key={`hidden-${e.position}`} className="rounded-md border border-dashed border-hairline p-2 text-sm text-muted" data-testid="cab-agenda-hidden" data-position={e.position}>
              <span className="mr-2 text-xs">{items.length + hidden.indexOf(e) + 1}.</span>
              {t("itsm.change.cab.hiddenItem")}
            </li>
          ))}
        </ol>
      )}

      {canEdit && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" disabled={draft === null || save.isPending} onClick={() => save.mutate()} data-testid="cab-agenda-save">
            {t("itsm.change.cab.saveAgenda")}
          </Button>
          {draft !== null && (
            <Button variant="ghost" size="sm" onClick={() => setDraft(null)} data-testid="cab-agenda-reset">
              {t("itsm.change.cab.discard")}
            </Button>
          )}
          {save.isError && (
            <span className="text-xs text-danger" role="alert" data-testid="cab-agenda-error">
              {changeErrorText(save.error, t)}
              {blocked && ` ${t("itsm.change.cab.blockedLine", { tn: blocked.tn })}`}
            </span>
          )}
        </div>
      )}

      {canEdit && (
        <div className="space-y-1 border-t border-hairline pt-2" data-testid="cab-suggestions">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{t("itsm.change.cab.suggestions")}</h3>
          {suggestionsQ.isError ? (
            <p className="text-xs text-danger" role="alert" data-testid="cab-suggestions-error">
              {changeErrorText(suggestionsQ.error, t)}
            </p>
          ) : suggestions.length === 0 ? (
            <p className="text-xs text-muted" data-testid="cab-suggestions-empty">
              {t("itsm.change.cab.suggestionsEmpty")}
            </p>
          ) : (
            <ul className="space-y-1">
              {suggestions.map((s) => (
                <li key={s.ticket_id} className="flex flex-wrap items-center justify-between gap-2 text-sm" data-testid={`cab-suggestion-${s.ticket_id}`}>
                  <span className="min-w-0 break-words text-ink">
                    <span className="font-mono text-xs text-muted">{s.tn}</span> {s.title}
                  </span>
                  <Button size="sm" onClick={() => add(s)} data-testid={`cab-suggestion-add-${s.ticket_id}`}>
                    {t("itsm.change.cab.add")}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function MinutesSection({ meeting, canManage }: { meeting: CabMeeting; canManage: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [minutes, setMinutes] = useState<string | null>(null);
  const [status, setStatus] = useState<CabMeeting["status"] | null>(null);
  const shownMinutes = minutes ?? meeting.minutes ?? "";
  const shownStatus = status ?? meeting.status;
  const dirty = minutes !== null || status !== null;

  const save = useMutation({
    mutationFn: () => changeApi.updateMeeting(meeting.id, { ...(minutes !== null ? { minutes } : {}), ...(status !== null ? { status } : {}) }),
    onSuccess: (saved) => {
      qc.setQueryData(changeKeys.meeting(meeting.id), saved);
      void qc.invalidateQueries({ queryKey: [...changeKeys.all(), "meetings"] });
      setMinutes(null);
      setStatus(null);
    },
  });

  return (
    <section className={SECTION_CLASS} data-testid="cab-minutes">
      <h2 className="text-sm font-semibold text-ink">{t("itsm.change.cab.minutes")}</h2>
      <p className="text-xs text-muted" data-testid="cab-minutes-hint">
        {t("itsm.change.cab.minutesHint")}
      </p>
      {canManage ? (
        <>
          <textarea
            className={`${FIELD_CLASS} min-h-32`}
            aria-label={t("itsm.change.cab.minutes")}
            maxLength={100000}
            value={shownMinutes}
            data-testid="cab-minutes-input"
            onChange={(e) => setMinutes(e.target.value)}
          />
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-48">
              <label className={LABEL_CLASS} htmlFor="cab-status-select">
                {t("itsm.change.cab.statusLabel")}
              </label>
              <SelectField
                id="cab-status-select"
                testId="cab-status-select"
                items={STATUSES.map((s) => ({ value: s, label: t(`itsm.change.cab.status.${s}`) }))}
                value={shownStatus}
                onChange={(v) => setStatus(v === meeting.status ? null : v)}
              />
            </div>
            <Button variant="primary" size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()} data-testid="cab-minutes-save">
              {t("itsm.change.cab.saveMinutes")}
            </Button>
            {save.isError && (
              <span className="text-xs text-danger" role="alert" data-testid="cab-minutes-error">
                {changeErrorText(save.error, t)}
              </span>
            )}
          </div>
        </>
      ) : meeting.minutes ? (
        <p className="whitespace-pre-wrap break-words text-sm text-ink" data-testid="cab-minutes-text">
          {meeting.minutes}
        </p>
      ) : (
        <p className="text-sm text-muted" data-testid="cab-minutes-empty">
          {t("itsm.change.cab.minutesEmpty")}
        </p>
      )}
    </section>
  );
}
