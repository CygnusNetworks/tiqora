import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import type { CalendarOut, RecurrenceIn } from "@/lib/api";
import type { AppointmentFormValue } from "./appointmentForm";

const RECUR_TYPES: RecurrenceIn["type"][] = ["Daily", "Weekly", "Monthly", "Yearly"];

export function AppointmentDialog({
  open,
  onClose,
  calendars,
  initial,
  editing,
  onSave,
  onDelete,
  saving,
  error,
}: {
  open: boolean;
  onClose: () => void;
  calendars: CalendarOut[];
  initial: AppointmentFormValue;
  editing: boolean;
  onSave: (value: AppointmentFormValue) => void;
  onDelete?: () => void;
  saving?: boolean;
  error?: string | null;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<AppointmentFormValue>(initial);

  useEffect(() => {
    setForm(initial);
  }, [initial, open]);

  const inputClass =
    "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";
  const labelClass = "mb-1 block text-xs font-medium uppercase tracking-wide text-muted";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={editing ? t("calendar.editAppointment") : t("calendar.newAppointment")}
      className="max-w-lg"
    >
      <form
        data-testid="appointment-form"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(form);
        }}
        className="flex max-h-[70vh] flex-col gap-3 overflow-y-auto"
      >
        <div>
          <span className={labelClass}>{t("calendar.form.titleLabel")}</span>
          <input
            data-testid="appointment-title"
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            className={inputClass}
            required
          />
        </div>

        <div>
          <span className={labelClass}>{t("calendar.form.calendar")}</span>
          <select
            data-testid="appointment-calendar"
            value={form.calendar_id}
            onChange={(e) => setForm((f) => ({ ...f, calendar_id: Number(e.target.value) }))}
            className={inputClass}
          >
            {calendars.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <span className={labelClass}>{t("calendar.form.start")}</span>
            <input
              data-testid="appointment-start"
              type="datetime-local"
              value={form.start_time}
              onChange={(e) => setForm((f) => ({ ...f, start_time: e.target.value }))}
              className={inputClass}
              required
            />
          </div>
          <div>
            <span className={labelClass}>{t("calendar.form.end")}</span>
            <input
              data-testid="appointment-end"
              type="datetime-local"
              value={form.end_time}
              onChange={(e) => setForm((f) => ({ ...f, end_time: e.target.value }))}
              className={inputClass}
              required
            />
          </div>
        </div>

        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            data-testid="appointment-all-day"
            type="checkbox"
            checked={form.all_day}
            onChange={(e) => setForm((f) => ({ ...f, all_day: e.target.checked }))}
          />
          {t("calendar.form.allDay")}
        </label>

        <div>
          <span className={labelClass}>{t("calendar.form.location")}</span>
          <input
            data-testid="appointment-location"
            value={form.location}
            onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))}
            className={inputClass}
          />
        </div>

        <div>
          <span className={labelClass}>{t("calendar.form.description")}</span>
          <textarea
            data-testid="appointment-description"
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            className={inputClass}
            rows={3}
          />
        </div>

        <div>
          <span className={labelClass}>{t("calendar.form.recurrence")}</span>
          <select
            data-testid="appointment-recurrence-type"
            value={form.recurrence.type}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                recurrence: { ...f.recurrence, type: e.target.value as RecurrenceIn["type"] | "" },
              }))
            }
            className={inputClass}
          >
            <option value="">{t("calendar.form.recurrenceNone")}</option>
            {RECUR_TYPES.map((r) => (
              <option key={r} value={r}>
                {t(`calendar.recurrence.${r}`)}
              </option>
            ))}
          </select>
        </div>

        {form.recurrence.type && (
          <div className="grid grid-cols-3 gap-2">
            <div>
              <span className={labelClass}>{t("calendar.form.recurrenceInterval")}</span>
              <input
                data-testid="appointment-recurrence-interval"
                type="number"
                min={1}
                value={form.recurrence.interval}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    recurrence: { ...f.recurrence, interval: Number(e.target.value) || 1 },
                  }))
                }
                className={inputClass}
              />
            </div>
            <div>
              <span className={labelClass}>{t("calendar.form.recurrenceCount")}</span>
              <input
                data-testid="appointment-recurrence-count"
                type="number"
                min={1}
                value={form.recurrence.count}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    recurrence: { ...f.recurrence, count: e.target.value },
                  }))
                }
                className={inputClass}
              />
            </div>
            <div>
              <span className={labelClass}>{t("calendar.form.recurrenceUntil")}</span>
              <input
                data-testid="appointment-recurrence-until"
                type="date"
                value={form.recurrence.until.slice(0, 10)}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    recurrence: { ...f.recurrence, until: e.target.value },
                  }))
                }
                className={inputClass}
              />
            </div>
          </div>
        )}

        {error && (
          <p className="text-sm text-escalation" data-testid="appointment-form-error">
            {error}
          </p>
        )}

        <div className="flex justify-between gap-2 border-t border-hairline pt-3">
          <div>
            {editing && onDelete && (
              <Button
                type="button"
                variant="danger"
                size="sm"
                data-testid="appointment-delete"
                onClick={onDelete}
              >
                {t("calendar.form.delete")}
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("calendar.form.cancel")}
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={saving}
              data-testid="appointment-form-submit"
            >
              {t("calendar.form.save")}
            </Button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}
