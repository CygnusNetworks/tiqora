import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import type { AppointmentOut, OccurrenceOut } from "@/lib/api";
import { addDays, dayKey, groupByDay, isSameDay, monthGridDays, weekDays } from "@/lib/calendarMath";
import { MonthGrid } from "@/components/agent/calendar/MonthGrid";
import { AppointmentDialog } from "@/components/agent/calendar/AppointmentDialog";
import {
  defaultFormValue,
  formValueFromAppointment,
  type AppointmentFormValue,
} from "@/components/agent/calendar/appointmentForm";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

const CALENDAR_PALETTE = [
  "#93c5fd",
  "#fca5a5",
  "#86efac",
  "#fcd34d",
  "#c4b5fd",
  "#f9a8d4",
];

type ViewMode = "month" | "week" | "agenda";

function toIso(localDateTime: string): string {
  // datetime-local has no timezone; interpret as local time.
  const d = new Date(localDateTime);
  return d.toISOString();
}

function fromFormValue(form: AppointmentFormValue) {
  const recurrence =
    form.recurrence.type === ""
      ? undefined
      : {
          type: form.recurrence.type,
          interval: form.recurrence.interval || 1,
          count: form.recurrence.count ? Number(form.recurrence.count) : undefined,
          until: form.recurrence.until ? new Date(form.recurrence.until).toISOString() : undefined,
        };
  return {
    calendar_id: form.calendar_id,
    title: form.title,
    description: form.description || null,
    location: form.location || null,
    start_time: toIso(form.start_time),
    end_time: toIso(form.end_time),
    all_day: form.all_day,
    recurrence,
  };
}

export function CalendarPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [view, setView] = useState<ViewMode>("month");
  const [anchor, setAnchor] = useState(new Date());
  const [selectedCalendarIds, setSelectedCalendarIds] = useState<Set<number> | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingAppointment, setEditingAppointment] = useState<AppointmentOut | null>(null);
  const [formValue, setFormValue] = useState<AppointmentFormValue | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const calendarsQ = useQuery({
    queryKey: ["calendar", "calendars"],
    queryFn: () => api.listCalendars(),
  });

  const calendarColors = useMemo(() => {
    const out: Record<number, string> = {};
    (calendarsQ.data ?? []).forEach((c, i) => {
      out[c.id] = CALENDAR_PALETTE[i % CALENDAR_PALETTE.length];
    });
    return out;
  }, [calendarsQ.data]);

  const { rangeStart, rangeEnd } = useMemo(() => {
    if (view === "week") {
      const days = weekDays(anchor);
      return { rangeStart: days[0], rangeEnd: addDays(days[6], 1) };
    }
    if (view === "agenda") {
      return { rangeStart: anchor, rangeEnd: addDays(anchor, 30) };
    }
    const days = monthGridDays(anchor);
    return { rangeStart: days[0], rangeEnd: addDays(days[41], 1) };
  }, [anchor, view]);

  const activeCalendarIds =
    selectedCalendarIds ?? new Set((calendarsQ.data ?? []).map((c) => c.id));

  const occurrencesQ = useQuery({
    queryKey: [
      "calendar",
      "appointments",
      rangeStart.toISOString(),
      rangeEnd.toISOString(),
      [...activeCalendarIds].sort(),
    ],
    queryFn: () =>
      api.listAppointments({
        start: rangeStart.toISOString(),
        end: rangeEnd.toISOString(),
        calendar_id: [...activeCalendarIds],
      }),
    enabled: (calendarsQ.data ?? []).length > 0,
  });

  const occurrences = (occurrencesQ.data ?? []).filter((o) => activeCalendarIds.has(o.calendar_id));

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["calendar", "appointments"] });
  };

  const createM = useMutation({
    mutationFn: (value: AppointmentFormValue) => api.createAppointment(fromFormValue(value)),
    onSuccess: () => {
      invalidate();
      setDialogOpen(false);
    },
  });

  const updateM = useMutation({
    mutationFn: (value: AppointmentFormValue) =>
      api.updateAppointment(editingAppointment!.id, fromFormValue(value)),
    onSuccess: () => {
      invalidate();
      setDialogOpen(false);
    },
  });

  const deleteM = useMutation({
    mutationFn: () => api.deleteAppointment(editingAppointment!.id),
    onSuccess: () => {
      invalidate();
      setDialogOpen(false);
    },
  });

  const openCreateDialog = (day?: Date) => {
    const calId = [...activeCalendarIds][0] ?? calendarsQ.data?.[0]?.id;
    if (!calId) return;
    const start = day ? new Date(day) : new Date();
    if (day) start.setHours(9, 0, 0, 0);
    setEditingAppointment(null);
    setFormValue(defaultFormValue(calId, start));
    setFormError(null);
    setDialogOpen(true);
  };

  const openEditDialog = async (occ: OccurrenceOut) => {
    setFormError(null);
    try {
      const appt = await api.getAppointment(occ.appointment_id);
      setEditingAppointment(appt);
      setFormValue(formValueFromAppointment(appt));
      setDialogOpen(true);
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const handleSave = async (value: AppointmentFormValue) => {
    setFormError(null);
    try {
      if (editingAppointment) {
        await updateM.mutateAsync(value);
      } else {
        await createM.mutateAsync(value);
      }
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const handleDelete = async () => {
    setFormError(null);
    try {
      await deleteM.mutateAsync();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const navigate = (delta: number) => {
    if (view === "week") setAnchor((a) => addDays(a, delta * 7));
    else if (view === "agenda") setAnchor((a) => addDays(a, delta * 30));
    else setAnchor((a) => new Date(a.getFullYear(), a.getMonth() + delta, 1));
  };

  const monthLabel = new Intl.DateTimeFormat(i18n.language, {
    month: "long",
    year: "numeric",
  }).format(anchor);

  const agendaGroups = useMemo(() => {
    const grouped = groupByDay(occurrences);
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [occurrences]);

  return (
    <div className="flex min-h-0 flex-1" data-testid="calendar-page">
      <aside className="hidden w-56 shrink-0 overflow-y-auto border-r border-hairline bg-surface p-3 md:block">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          {t("calendar.sidebar")}
        </h2>
        {calendarsQ.isLoading ? (
          <Spinner />
        ) : (calendarsQ.data ?? []).length === 0 ? (
          <p className="text-sm text-muted">{t("calendar.noCalendars")}</p>
        ) : (
          <ul className="space-y-1" data-testid="calendar-switcher">
            {(calendarsQ.data ?? []).map((c) => (
              <li key={c.id}>
                <label className="flex items-center gap-2 text-sm text-ink">
                  <input
                    type="checkbox"
                    data-testid={`calendar-toggle-${c.id}`}
                    checked={activeCalendarIds.has(c.id)}
                    onChange={(e) => {
                      setSelectedCalendarIds((prev) => {
                        const base = prev ?? new Set((calendarsQ.data ?? []).map((cc) => cc.id));
                        const next = new Set(base);
                        if (e.target.checked) next.add(c.id);
                        else next.delete(c.id);
                        return next;
                      });
                    }}
                  />
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: calendarColors[c.id] }}
                  />
                  <span className="truncate">{c.name}</span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col p-3">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h1 className="font-display text-lg font-semibold text-ink">{t("calendar.title")}</h1>
          <div className="ml-2 flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={() => navigate(-1)} aria-label="prev">
              ‹
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setAnchor(new Date())}>
              {t("calendar.today")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => navigate(1)} aria-label="next">
              ›
            </Button>
            <span className="ml-1 text-sm font-medium text-ink">{monthLabel}</span>
          </div>
          <div className="ml-2 flex gap-1 rounded-md border border-hairline p-0.5">
            {(["month", "week", "agenda"] as ViewMode[]).map((v) => (
              <button
                key={v}
                type="button"
                data-testid={`calendar-view-${v}`}
                onClick={() => setView(v)}
                className={`rounded px-2 py-1 text-xs font-medium ${
                  view === v ? "bg-accent text-accent-ink" : "text-muted hover:text-ink"
                }`}
              >
                {t(`calendar.view.${v}`)}
              </button>
            ))}
          </div>
          <div className="ml-auto">
            <Button
              variant="primary"
              size="sm"
              data-testid="calendar-new-appointment"
              onClick={() => openCreateDialog()}
              disabled={(calendarsQ.data ?? []).length === 0}
            >
              {t("calendar.newAppointment")}
            </Button>
          </div>
        </div>

        {occurrencesQ.isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <Spinner />
          </div>
        ) : view === "month" ? (
          <MonthGrid
            anchor={anchor}
            occurrences={occurrences}
            calendarColors={calendarColors}
            onSelectDay={openCreateDialog}
            onSelectOccurrence={(occ) => void openEditDialog(occ)}
          />
        ) : view === "week" ? (
          <div className="grid flex-1 grid-cols-7 gap-1" data-testid="calendar-week-view">
            {weekDays(anchor).map((day) => {
              const key = dayKey(day);
              const dayOccs = occurrences.filter((o) => isSameDay(new Date(o.start_time), day));
              return (
                <div
                  key={key}
                  className="flex flex-col gap-1 rounded-md border border-hairline p-1.5"
                  data-testid={`calendar-week-day-${key}`}
                >
                  <span className="text-xs font-semibold text-muted">
                    {new Intl.DateTimeFormat(i18n.language, { weekday: "short", day: "numeric" }).format(day)}
                  </span>
                  {dayOccs.map((occ) => (
                    <button
                      type="button"
                      key={`${occ.appointment_id}-${occ.start_time}`}
                      onClick={() => void openEditDialog(occ)}
                      className="truncate rounded px-1.5 py-1 text-left text-xs font-medium text-ink"
                      style={{ backgroundColor: calendarColors[occ.calendar_id] }}
                    >
                      {occ.title}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto" data-testid="calendar-agenda-view">
            {agendaGroups.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">{t("calendar.empty")}</p>
            ) : (
              agendaGroups.map(([day, items]) => (
                <div key={day} className="mb-3">
                  <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                    {new Intl.DateTimeFormat(i18n.language, { dateStyle: "full" }).format(
                      new Date(day),
                    )}
                  </h3>
                  <ul className="space-y-1">
                    {items.map((occ) => (
                      <li key={`${occ.appointment_id}-${occ.start_time}`}>
                        <button
                          type="button"
                          onClick={() => void openEditDialog(occ)}
                          className="flex w-full items-center gap-2 rounded-md border border-hairline bg-surface px-3 py-2 text-left text-sm hover:bg-surface-subtle"
                        >
                          <span
                            className="h-2.5 w-2.5 shrink-0 rounded-full"
                            style={{ backgroundColor: calendarColors[occ.calendar_id] }}
                          />
                          <span className="font-medium text-ink">{occ.title}</span>
                          <span className="ml-auto text-xs text-muted">
                            {new Intl.DateTimeFormat(i18n.language, { timeStyle: "short" }).format(
                              new Date(occ.start_time),
                            )}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {formValue && (
        <AppointmentDialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          calendars={calendarsQ.data ?? []}
          initial={formValue}
          editing={!!editingAppointment}
          onSave={(v) => void handleSave(v)}
          onDelete={editingAppointment ? () => void handleDelete() : undefined}
          saving={createM.isPending || updateM.isPending}
          error={formError}
        />
      )}
    </div>
  );
}
