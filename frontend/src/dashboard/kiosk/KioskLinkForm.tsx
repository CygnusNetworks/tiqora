import { useId, useState, type FormEvent } from "react";
import { useQueries } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { dashboardKeys, dashboardsApi, type DashboardTab, type KioskLinkIn } from "@/lib/dashboardsApi";
import { dashboardRows, hasMineWidget, WALLBOARD_MAX_ROWS } from "./kioskLinks";

const NAME_MAX = 120;
const MAX_DASHBOARDS = 10;
const ROTATE = { min: 15, max: 900, default: 60 } as const;
const EXPIRES = { min: 1, max: 365, default: 90 } as const;
const INPUT =
  "rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

const inRange = (raw: string, min: number, max: number): number | null => {
  if (!/^\d+$/.test(raw.trim())) return null;
  const n = Number.parseInt(raw, 10);
  return n >= min && n <= max ? n : null;
};

/**
 * The fields of a new wallboard link. Preselects the dashboard it was opened from. Under the list it
 * warns about dashboards that are too tall for a TV and about "mine" tiles, which on a wallboard show
 * the queue of whoever creates the link. Validation is local; the server's own 422 arrives as `error`.
 */
export function KioskLinkForm({
  dashboards,
  initialDashboardId,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  dashboards: DashboardTab[];
  initialDashboardId: number | null;
  pending: boolean;
  error: string | null;
  onSubmit: (body: KioskLinkIn) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const ids = useId();
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<number[]>(() =>
    initialDashboardId !== null && dashboards.some((d) => d.id === initialDashboardId) ? [initialDashboardId] : [],
  );
  const [rotate, setRotate] = useState(String(ROTATE.default));
  const [expires, setExpires] = useState(String(EXPIRES.default));
  const [details, setDetails] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const full = useQueries({
    queries: picked.map((id) => ({
      queryKey: dashboardKeys.one(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => dashboardsApi.get(id, signal),
      staleTime: 30_000,
    })),
  });
  const tall = full.flatMap((q) => (q.data && dashboardRows(q.data) > WALLBOARD_MAX_ROWS ? [q.data] : []));
  const mine = full.flatMap((q) => (q.data && hasMineWidget(q.data) ? [q.data] : []));

  const trimmed = name.trim();
  const rotateN = inRange(rotate, ROTATE.min, ROTATE.max);
  const expiresN = inRange(expires, EXPIRES.min, EXPIRES.max);
  const nameError = submitted && !trimmed ? t("dashboards.kiosk.nameRequired") : null;
  const dashboardsError = submitted && picked.length === 0 ? t("dashboards.kiosk.dashboardsRequired") : null;
  const rotateError = submitted && rotateN === null ? t("dashboards.kiosk.rotateRange") : null;
  const expiresError = submitted && expiresN === null ? t("dashboards.kiosk.expiresRange") : null;

  const toggle = (id: number) =>
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : prev.length >= MAX_DASHBOARDS ? prev : [...prev, id]));

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (pending) return;
    setSubmitted(true);
    if (!trimmed || picked.length === 0 || rotateN === null || expiresN === null) return;
    onSubmit({
      name: trimmed,
      dashboard_ids: picked,
      rotate_seconds: rotateN,
      expires_in_days: expiresN,
      show_details: details,
    });
  };

  return (
    <form onSubmit={submit} className="space-y-4" noValidate data-testid="kiosk-link-form">
      <div className="space-y-1.5">
        <label htmlFor={`${ids}-name`} className="text-[13px] font-medium text-ink">
          {t("dashboards.kiosk.name")}
        </label>
        <input
          id={`${ids}-name`}
          type="text"
          value={name}
          maxLength={NAME_MAX}
          onChange={(e) => setName(e.target.value)}
          aria-invalid={nameError ? true : undefined}
          aria-describedby={nameError ? `${ids}-name-err` : undefined}
          className={`${INPUT} w-full`}
        />
        {nameError && (
          <p id={`${ids}-name-err`} className="text-xs text-danger">
            {nameError}
          </p>
        )}
      </div>

      <fieldset className="space-y-1.5" aria-describedby={dashboardsError ? `${ids}-dash-err` : undefined}>
        <legend className="text-[13px] font-medium text-ink">{t("dashboards.kiosk.dashboards")}</legend>
        <ul className="max-h-44 space-y-1 overflow-y-auto rounded-md border border-hairline p-2">
          {dashboards.map((d) => (
            <li key={d.id}>
              <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={picked.includes(d.id)}
                  disabled={!picked.includes(d.id) && picked.length >= MAX_DASHBOARDS}
                  onChange={() => toggle(d.id)}
                />
                {d.name}
              </label>
            </li>
          ))}
        </ul>
        {picked.length >= MAX_DASHBOARDS && <p className="text-xs text-muted">{t("dashboards.kiosk.dashboardsMax")}</p>}
        {dashboardsError && (
          <p id={`${ids}-dash-err`} className="text-xs text-danger">
            {dashboardsError}
          </p>
        )}
        {full.some((q) => q.isFetching && !q.data) && <Spinner />}
        {tall.map((d) => (
          <p key={`tall-${d.id}`} role="status" className="text-xs text-warn">
            {t("dashboards.kiosk.tooTall", { name: d.name })}
          </p>
        ))}
        {mine.map((d) => (
          <p key={`mine-${d.id}`} role="status" className="text-xs text-warn">
            {t("dashboards.kiosk.mine", { name: d.name })}
          </p>
        ))}
      </fieldset>

      <div className="flex flex-wrap gap-6">
        <div className="space-y-1.5">
          <label htmlFor={`${ids}-rotate`} className="text-[13px] font-medium text-ink">
            {t("dashboards.kiosk.rotate")}
          </label>
          <div className="flex items-center gap-2">
            <input
              id={`${ids}-rotate`}
              type="number"
              inputMode="numeric"
              min={ROTATE.min}
              max={ROTATE.max}
              value={rotate}
              onChange={(e) => setRotate(e.target.value)}
              aria-invalid={rotateError ? true : undefined}
              aria-describedby={rotateError ? `${ids}-rotate-err` : undefined}
              className={`${INPUT} w-24`}
            />
            <span className="text-sm text-muted">{t("dashboards.kiosk.seconds")}</span>
          </div>
          {rotateError && (
            <p id={`${ids}-rotate-err`} className="text-xs text-danger">
              {rotateError}
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <label htmlFor={`${ids}-expires`} className="text-[13px] font-medium text-ink">
            {t("dashboards.kiosk.expires")}
          </label>
          <div className="flex items-center gap-2">
            <input
              id={`${ids}-expires`}
              type="number"
              inputMode="numeric"
              min={EXPIRES.min}
              max={EXPIRES.max}
              value={expires}
              onChange={(e) => setExpires(e.target.value)}
              aria-invalid={expiresError ? true : undefined}
              aria-describedby={expiresError ? `${ids}-expires-err` : undefined}
              className={`${INPUT} w-24`}
            />
            <span className="text-sm text-muted">{t("dashboards.kiosk.days")}</span>
          </div>
          {expiresError && (
            <p id={`${ids}-expires-err`} className="text-xs text-danger">
              {expiresError}
            </p>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <Switch checked={details} onChange={setDetails} aria-labelledby={`${ids}-details`} />
        <span id={`${ids}-details`} className="text-sm text-ink">
          {t("dashboards.kiosk.details")}
        </span>
      </div>

      <p className="rounded-md bg-surface-subtle px-3 py-2 text-xs text-muted">{t("dashboards.kiosk.rights")}</p>

      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel} disabled={pending}>
          {t("dashboards.kiosk.cancel")}
        </Button>
        <Button variant="primary" type="submit" disabled={pending}>
          {pending ? <Spinner /> : t("dashboards.kiosk.create")}
        </Button>
      </div>
    </form>
  );
}
