import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import type { NotificationTransport } from "@/lib/pluginPointsApi";

/** Znuny's built-in transport; every other key comes from a plugin. */
const EMAIL = "Email";

/**
 * "Versandwege" of one notification: the stored `Transports` item rows.
 * Values that are not on the offered list (a switched-off plugin, a transport
 * Znuny knows) stay in the list unchanged and are shown read-only by key.
 * The backend treats a missing/empty list as "e-mail on", so an empty stored
 * list is shown as E-Mail checked, and saving an empty selection is blocked.
 */
export function NotificationTransports({
  current,
  transports,
  saving,
  onSave,
}: {
  current: string[];
  transports: NotificationTransport[];
  saving?: boolean;
  onSave: (next: string[]) => void;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<string[]>(current.length ? current : [EMAIL]);
  const offered = [
    { key: EMAIL, label: t("admin.notificationEvents.transportEmail") },
    ...transports.filter((tr) => tr.key !== EMAIL).map((tr) => ({ key: tr.key, label: tr.label })),
  ];
  const offeredKeys = new Set(offered.map((o) => o.key));
  const unknown = selected.filter((k) => !offeredKeys.has(k));
  const toggle = (key: string, on: boolean) =>
    setSelected((prev) => (on ? (prev.includes(key) ? prev : [...prev, key]) : prev.filter((k) => k !== key)));
  return (
    <form
      className="space-y-2 rounded-md border border-line bg-surface-subtle p-3 text-sm"
      data-testid="notification-transports"
      onSubmit={(e) => {
        e.preventDefault();
        if (selected.length === 0) return;
        onSave(selected);
      }}
    >
      <fieldset className="space-y-1.5">
        <legend className="mb-1 font-medium text-ink">{t("admin.notificationEvents.transports")}</legend>
        {offered.map((o) => (
          <label key={o.key} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={selected.includes(o.key)}
              onChange={(e) => toggle(o.key, e.target.checked)}
            />
            <span>{o.label}</span>
          </label>
        ))}
        {unknown.map((key) => (
          <label key={key} className="flex items-center gap-2 text-muted">
            <input type="checkbox" checked disabled readOnly />
            <span data-testid={`notification-transport-unknown-${key}`}>{key}</span>
          </label>
        ))}
      </fieldset>
      {selected.length === 0 && (
        <p className="text-xs text-muted" data-testid="notification-transports-empty">
          {t("admin.notificationEvents.transportsEmptyHint")}
        </p>
      )}
      <Button type="submit" size="sm" disabled={saving || selected.length === 0}>
        {t("admin.form.save")}
      </Button>
    </form>
  );
}
