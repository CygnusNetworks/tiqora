import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { SelectMenu } from "@/components/ui/SelectMenu";
import { ClockIcon } from "@/components/ui/icons";
import { browserTimeZone, timeZoneMismatch, type TimeZoneMismatch as Mismatch } from "@/lib/timeZone";
import { useTimeZoneSetting } from "./useTimeZoneSetting";

const DISMISS_KEY = "tiqora.timeZoneHint.dismissed";

/** The dismissal sticks to this exact mismatch: a new one shows again. */
function signature(userId: number, m: Mismatch): string {
  return m.kind === "browserVsSystem"
    ? `${userId}|-|${m.browser}|${m.system}`
    : `${userId}|${m.zone}|${m.browser}`;
}

function readDismissed(): string | null {
  try {
    return window.localStorage.getItem(DISMISS_KEY);
  } catch {
    return null;
  }
}

function writeDismissed(value: string) {
  try {
    window.localStorage.setItem(DISMISS_KEY, value);
  } catch {
    // Storage blocked — the notice just stays dismissed for this render tree.
  }
}

/**
 * Slim notice under the agent header when the zone times are shown in (or
 * the zone e-mails/notifications use) differs from this device's zone, with
 * a one-click fix and the zone picker. Dismissible per mismatch.
 */
export function TimeZoneHint() {
  const { user } = useAuth();
  const [dismissed, setDismissed] = useState(readDismissed);
  if (!user || typeof user.id !== "number") return null;
  const mismatch = timeZoneMismatch(user.time_zone, user.default_time_zone, browserTimeZone());
  if (!mismatch) return null;
  const sig = signature(user.id, mismatch);
  if (dismissed === sig) return null;
  return (
    <TimeZoneHintBar
      mismatch={mismatch}
      onDismiss={() => {
        writeDismissed(sig);
        setDismissed(sig);
      }}
    />
  );
}

function TimeZoneHintBar({ mismatch, onDismiss }: { mismatch: Mismatch; onDismiss: () => void }) {
  const { t } = useTranslation();
  const tz = useTimeZoneSetting();
  const linkCls =
    "rounded font-medium text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-60";
  return (
    <div
      role="status"
      data-testid="tz-hint"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-hairline bg-accent-dim px-4 py-1.5 text-[12.5px] text-ink"
    >
      <ClockIcon className="shrink-0 text-[14px] text-accent" aria-hidden />
      <span className="min-w-0">
        {mismatch.kind === "browserVsSystem"
          ? t("timeZoneHint.browserVsSystem", { browser: mismatch.browser, system: mismatch.system })
          : t("timeZoneHint.settingVsDevice", { zone: mismatch.zone, browser: mismatch.browser })}
      </span>
      {mismatch.kind === "browserVsSystem" && (
        <button
          type="button"
          data-testid="tz-hint-use-browser"
          className={linkCls}
          disabled={tz.isPending}
          onClick={() => tz.save(mismatch.browser)}
        >
          {t("timeZoneHint.useZone", { zone: mismatch.browser })}
        </button>
      )}
      <SelectMenu
        items={tz.items}
        value={tz.value}
        onSelect={tz.save}
        panelTestId="tz-hint-panel"
        trigger={({ ref, toggleProps }) => (
          <button
            ref={ref}
            type="button"
            data-testid="tz-hint-choose"
            className={linkCls}
            disabled={tz.isPending}
            {...toggleProps}
          >
            {t("timeZoneHint.choose")}
          </button>
        )}
      />
      {tz.isError && <span className="text-danger">{t("account.timeZoneError")}</span>}
      <button
        type="button"
        data-testid="tz-hint-dismiss"
        aria-label={t("timeZoneHint.dismiss")}
        title={t("timeZoneHint.dismiss")}
        onClick={onDismiss}
        className="ml-auto flex h-6 w-6 items-center justify-center rounded text-muted hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      >
        ×
      </button>
    </div>
  );
}
