import { useTranslation } from "react-i18next";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import { setValues } from "@/lib/webchatForm";
import { Field, NumberInput, inputClass, type TabProps } from "./shared";

const CALENDARS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

/** API: `null` = always open, `""` = default calendar, `"1"`..`"9"` = calendar N. */
function calendarToChoice(v: string | null | undefined): string {
  return v === null || v === undefined ? "always" : v === "" ? "default" : v;
}
function choiceToCalendar(c: string): string | null {
  return c === "always" ? null : c === "default" ? "" : c;
}

export function HoursTab({ draft, errors, onDraft }: TabProps) {
  const { t } = useTranslation();
  const v = draft.values;
  const items = [
    { value: "always", label: t("webchat.admin.hours.always") },
    { value: "default", label: t("webchat.admin.hours.defaultCalendar") },
    ...CALENDARS.map((n) => ({ value: n, label: t("webchat.admin.hours.calendarN", { n }) })),
  ];
  return (
    <div className="space-y-5" data-testid="webchat-tab-hours">
      <Field id="calendar" label={t("webchat.admin.hours.hours")} hint={t("webchat.admin.hours.hoursHint")} error={errors.business_calendar}>
        <SelectField
          id="webchat-calendar"
          testId="webchat-calendar"
          items={items}
          value={calendarToChoice(v.business_calendar)}
          onChange={(c) => onDraft(setValues(draft, { business_calendar: choiceToCalendar(c) }))}
        />
      </Field>
      <Field
        id="wait-timeout"
        label={t("webchat.admin.hours.waitTimeout")}
        hint={t("webchat.admin.hours.waitTimeoutHint")}
        error={errors.wait_timeout_seconds}
        className="max-w-xs"
      >
        <NumberInput
          id="wait-timeout"
          min={30}
          max={3600}
          value={v.wait_timeout_seconds}
          error={errors.wait_timeout_seconds}
          onChange={(wait_timeout_seconds) => onDraft(setValues(draft, { wait_timeout_seconds }))}
        />
      </Field>
      <div>
        <span id="webchat-offline-label" className="mb-1 block text-xs font-medium text-muted">
          {t("webchat.admin.hours.offline")}
        </span>
        <SegmentedControl
          testId="webchat-offline-mode"
          aria-labelledby="webchat-offline-label"
          items={[
            { value: "form", label: t("webchat.admin.hours.offlineForm") },
            { value: "hide", label: t("webchat.admin.hours.offlineHide") },
          ]}
          value={v.offline_mode}
          onChange={(offline_mode) => onDraft(setValues(draft, { offline_mode }))}
        />
      </div>
      <Field
        id="default-customer"
        label={t("webchat.admin.hours.defaultCustomer")}
        hint={t("webchat.admin.hours.defaultCustomerHint")}
        error={errors.default_customer_user}
        className="max-w-md"
      >
        <input
          id="webchat-default-customer"
          data-testid="webchat-default-customer"
          className={inputClass}
          value={v.default_customer_user ?? ""}
          aria-invalid={!!errors.default_customer_user}
          onChange={(e) => onDraft(setValues(draft, { default_customer_user: e.target.value }))}
        />
      </Field>
    </div>
  );
}
