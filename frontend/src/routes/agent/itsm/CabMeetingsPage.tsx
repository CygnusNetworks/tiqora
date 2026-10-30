import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { ChangeLinkRow } from "@/components/itsm/change/ChangeLinkRow";
import { MEETING_STATUS_TONE, shiftMonth, utcMonthRange } from "@/components/itsm/change/cabUi";
import { changeErrorText, FIELD_CLASS, fromLocalInput, LABEL_CLASS, utcRangeText } from "@/components/itsm/change/changeUi";
import { toBcp47 } from "@/i18n";
import { changeApi, changeErrorCode, changeKeys, type CabMeetingListItem } from "@/lib/changeApi";

/**
 * `/agent/itsm/cab-meetings`: the CAB meetings of one month (UTC); change managers plan a new
 * meeting from here. A meeting opens its own page (agenda, approvals, minutes).
 */
export function CabMeetingsPage() {
  const { t, i18n } = useTranslation();
  const [now] = useState(() => new Date());
  const [cursor, setCursor] = useState({ year: now.getUTCFullYear(), month: now.getUTCMonth() });
  const [creating, setCreating] = useState(false);
  const { start, end } = useMemo(() => utcMonthRange(cursor.year, cursor.month), [cursor]);
  const label = new Intl.DateTimeFormat(toBcp47(i18n.language), { month: "long", year: "numeric", timeZone: "UTC" }).format(
    new Date(Date.UTC(cursor.year, cursor.month, 1)),
  );

  const statusQ = useQuery({ queryKey: changeKeys.status(), queryFn: ({ signal }) => changeApi.status(signal), retry: false });
  const listQ = useQuery({
    queryKey: changeKeys.meetings(start, end),
    queryFn: ({ signal }) => changeApi.meetings(start, end, signal),
    retry: false,
  });
  const meetings = useMemo(() => [...(listQ.data ?? [])].sort((a, b) => a.starts_at.localeCompare(b.starts_at)), [listQ.data]);
  const code = listQ.isError ? changeErrorCode(listQ.error) : null;

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-6" data-testid="cabMeetingsPage">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-display text-xl font-semibold text-ink">{t("itsm.change.pages.cabMeetings")}</h1>
        {statusQ.data?.can_manage && (
          <Button variant="primary" size="sm" onClick={() => setCreating(true)} data-testid="cab-meetings-create">
            {t("itsm.change.cab.create")}
          </Button>
        )}
      </div>
      <ChangeLinkRow current="meetings" />
      <p className="text-xs text-muted">{t("itsm.change.cab.utcHint")}</p>

      <div className="flex items-center gap-1">
        <Button size="sm" variant="ghost" onClick={() => setCursor((c) => shiftMonth(c, -1))} aria-label={t("itsm.change.cab.prev")} data-testid="cab-meetings-prev">
          ‹
        </Button>
        <Button size="sm" variant="secondary" onClick={() => setCursor({ year: now.getUTCFullYear(), month: now.getUTCMonth() })} data-testid="cab-meetings-today">
          {t("calendar.today")}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setCursor((c) => shiftMonth(c, 1))} aria-label={t("itsm.change.cab.next")} data-testid="cab-meetings-next">
          ›
        </Button>
        <span className="ml-1 text-sm font-medium text-ink" data-testid="cab-meetings-label">
          {label}
        </span>
      </div>

      {listQ.isError ? (
        <div className="rounded-lg border border-hairline bg-surface px-4 py-6 text-center text-sm text-danger" role="alert" data-testid="cab-meetings-error" data-code={code ?? undefined}>
          <p>{changeErrorText(listQ.error, t)}</p>
          {code === "change_mode_a" && statusQ.data?.can_see_legacy && (
            <RouterAnchor href="/agent/itsm/legacy-changes" className="mt-2 inline-block text-accent hover:underline">
              {t("itsm.change.pages.legacyList")}
            </RouterAnchor>
          )}
        </div>
      ) : listQ.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : meetings.length === 0 ? (
        <p className="rounded-lg border border-hairline bg-surface px-4 py-8 text-center text-sm text-muted" data-testid="cab-meetings-empty">
          {t("itsm.change.cab.empty")}
        </p>
      ) : (
        <ul className="space-y-2" data-testid="cab-meetings-list">
          {meetings.map((m) => (
            <MeetingRow key={m.id} meeting={m} />
          ))}
        </ul>
      )}

      {creating && <CreateMeetingDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function MeetingRow({ meeting: m }: { meeting: CabMeetingListItem }) {
  const { t } = useTranslation();
  return (
    <li className="rounded-lg border border-hairline bg-surface px-4 py-3" data-testid={`cab-meeting-${m.id}`} data-status={m.status}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <RouterAnchor href={`/agent/itsm/cab-meetings/${m.id}`} className="min-w-0 break-words text-sm font-medium text-accent hover:underline">
          {m.title}
        </RouterAnchor>
        <Badge tone={MEETING_STATUS_TONE[m.status]}>{t(`itsm.change.cab.status.${m.status}`)}</Badge>
      </div>
      <p className="mt-1 break-words text-xs text-muted">
        {m.cab_name}
        {" · "}
        {utcRangeText(m.starts_at, m.ends_at)} UTC
        {m.location ? ` · ${m.location}` : ""}
        {" · "}
        {t("itsm.change.cab.agendaCount", { count: m.agenda_count })}
      </p>
    </li>
  );
}

function CreateMeetingDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const cabsQ = useQuery({ queryKey: changeKeys.cabs(), queryFn: ({ signal }) => changeApi.cabs(signal) });
  const [cabId, setCabId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [starts, setStarts] = useState("");
  const [ends, setEnds] = useState("");
  const [location, setLocation] = useState("");
  const startIso = fromLocalInput(starts);
  const endIso = fromLocalInput(ends);
  const orderBad = !!startIso && !!endIso && endIso <= startIso;
  const ready = cabId !== null && title.trim() !== "" && !!startIso && !!endIso && !orderBad;

  const create = useMutation({
    mutationFn: () =>
      changeApi.createMeeting({ cab_id: cabId as number, title: title.trim(), starts_at: startIso as string, ends_at: endIso as string, location: location.trim() || null }),
    onSuccess: (m) => {
      void qc.invalidateQueries({ queryKey: [...changeKeys.all(), "meetings"] });
      onClose();
      void navigate({ to: "/agent/itsm/cab-meetings/$meetingId", params: { meetingId: String(m.id) } });
    },
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("itsm.change.cab.create")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" disabled={!ready || create.isPending} onClick={() => create.mutate()} data-testid="cab-create-submit">
            {t("itsm.change.cab.createSubmit")}
          </Button>
        </>
      }
    >
      <div className="space-y-3" data-testid="cab-create-dialog">
        <div>
          <label className={LABEL_CLASS} htmlFor="cab-create-cab">
            {t("itsm.change.cab.cab")}
          </label>
          <SelectField
            id="cab-create-cab"
            testId="cab-create-cab"
            items={(cabsQ.data ?? []).map((c) => ({ value: c.id, label: c.name }))}
            value={cabId}
            onChange={setCabId}
            placeholder={t("itsm.change.cab.cabPlaceholder")}
          />
          {cabsQ.isError ? (
            <p className="mt-1 text-xs text-danger" role="alert" data-testid="cab-create-cabs-error">
              {changeErrorText(cabsQ.error, t)}
            </p>
          ) : (
            cabsQ.isSuccess &&
            cabsQ.data.length === 0 && (
              <p className="mt-1 text-xs text-muted" data-testid="cab-create-no-cabs">
                {t("itsm.change.cab.noCabs")}
              </p>
            )
          )}
        </div>
        <div>
          <label className={LABEL_CLASS} htmlFor="cab-create-title">
            {t("itsm.change.cab.title")}
          </label>
          <input id="cab-create-title" data-testid="cab-create-title" className={FIELD_CLASS} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className={LABEL_CLASS} htmlFor="cab-create-start">
              {t("itsm.change.cab.starts")}
            </label>
            <input id="cab-create-start" data-testid="cab-create-start" type="datetime-local" className={FIELD_CLASS} value={starts} onChange={(e) => setStarts(e.target.value)} />
          </div>
          <div>
            <label className={LABEL_CLASS} htmlFor="cab-create-end">
              {t("itsm.change.cab.ends")}
            </label>
            <input id="cab-create-end" data-testid="cab-create-end" type="datetime-local" className={FIELD_CLASS} value={ends} onChange={(e) => setEnds(e.target.value)} />
          </div>
        </div>
        {orderBad && (
          <p className="text-xs text-danger" role="alert" data-testid="cab-create-order">
            {t("itsm.change.errors.invalid_window")}
          </p>
        )}
        <div>
          <label className={LABEL_CLASS} htmlFor="cab-create-location">
            {t("itsm.change.cab.location")}
          </label>
          <input id="cab-create-location" data-testid="cab-create-location" className={FIELD_CLASS} maxLength={200} value={location} onChange={(e) => setLocation(e.target.value)} />
        </div>
        <p className="text-xs text-muted">{t("itsm.change.cab.utcHint")}</p>
        {create.isError && (
          <p className="text-xs text-danger" role="alert" data-testid="cab-create-error">
            {changeErrorText(create.error, t)}
          </p>
        )}
      </div>
    </Dialog>
  );
}
