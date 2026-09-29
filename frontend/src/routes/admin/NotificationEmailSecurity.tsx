import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import {
  SECURITY_LEVELS as LEVELS,
  firstItem as first,
  withSecurity,
  type Items,
} from "./notificationSecurityItems";

/**
 * Email security of one notification — Znuny's AdminNotificationEvent
 * "Email security" block (see notificationSecurityItems for the stored rows).
 */

export type EnabledBackends = { pgp: boolean; smime: boolean };

const selectClass =
  "rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent disabled:opacity-50";

export function NotificationEmailSecurity({
  items,
  backends,
  saving,
  onSave,
}: {
  items: Items;
  backends: EnabledBackends;
  saving?: boolean;
  onSave: (items: Items) => void;
}) {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(() => {
    const v = first(items, "EmailSecuritySettings");
    return Boolean(v) && v !== "0";
  });
  const [level, setLevel] = useState(() => first(items, "EmailSigningCrypting"));
  // Znuny's selects have no empty option: the first one (Skip) is the default.
  const [missingSign, setMissingSign] = useState(
    () => first(items, "EmailMissingSigningKeys") || "Skip",
  );
  const [missingCrypt, setMissingCrypt] = useState(
    () => first(items, "EmailMissingCryptingKeys") || "Skip",
  );

  const anyBackend = backends.pgp || backends.smime;
  const offered: string[] = [
    ...(backends.pgp ? LEVELS.pgp : []),
    ...(backends.smime ? LEVELS.smime : []),
  ];
  // A stored level whose backend is off stays visible so saving keeps it.
  if (level && !offered.includes(level)) offered.push(level);
  const levelBackend = level.startsWith("PGP") ? "pgp" : level.startsWith("SMIME") ? "smime" : "";
  const backendOff = enabled && levelBackend !== "" && !backends[levelBackend];

  return (
    <form
      className="space-y-3 rounded-md border border-line bg-surface-subtle p-3 text-sm"
      data-testid="notification-email-security"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(withSecurity(items, { enabled, level, missingSign, missingCrypt }));
      }}
    >
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={enabled}
          disabled={!anyBackend && !enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        <span>{t("admin.notificationEvents.security.enable")}</span>
      </label>
      {!anyBackend && (
        <p className="text-xs text-muted">{t("admin.notificationEvents.security.notEnabled")}</p>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1">
          <span>{t("admin.notificationEvents.security.level")}</span>
          <select
            className={selectClass}
            value={level}
            disabled={!enabled}
            onChange={(e) => setLevel(e.target.value)}
          >
            <option value="">{t("admin.notificationEvents.security.none")}</option>
            {offered.map((v) => (
              <option key={v} value={v}>
                {t(`admin.notificationEvents.security.levels.${v}`, { defaultValue: v })}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span>{t("admin.notificationEvents.security.missingSign")}</span>
          <select
            className={selectClass}
            value={missingSign}
            disabled={!enabled}
            onChange={(e) => setMissingSign(e.target.value)}
          >
            <option value="Skip">{t("admin.notificationEvents.security.skip")}</option>
            <option value="Send">{t("admin.notificationEvents.security.sendUnsigned")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span>{t("admin.notificationEvents.security.missingCrypt")}</span>
          <select
            className={selectClass}
            value={missingCrypt}
            disabled={!enabled}
            onChange={(e) => setMissingCrypt(e.target.value)}
          >
            <option value="Skip">{t("admin.notificationEvents.security.skip")}</option>
            <option value="Send">{t("admin.notificationEvents.security.sendUnencrypted")}</option>
          </select>
        </label>
      </div>
      {backendOff && (
        <p className="text-xs text-danger" role="alert">
          {t("admin.notificationEvents.security.backendOff", {
            backend: levelBackend === "pgp" ? "PGP" : "S/MIME",
          })}
        </p>
      )}
      <p className="text-xs text-muted">{t("admin.notificationEvents.security.hint")}</p>
      <Button type="submit" size="sm" disabled={saving}>
        {t("admin.notificationEvents.security.save")}
      </Button>
    </form>
  );
}
