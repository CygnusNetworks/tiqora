import { useState, type ComponentType, type ReactNode, type SVGProps } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import {
  CRYPTO_SETTINGS_KEY,
  CRYPTO_STATUS_KEY,
  useCryptoSettings,
  type CryptoBackend,
} from "@/components/admin/cryptoSettingsQuery";
import { Button } from "@/components/ui/Button";
import { Switch } from "@/components/ui/Switch";
import {
  ChevronLeftIcon,
  LockIcon,
  ServerIcon,
  SparkIcon,
} from "@/components/ui/icons";
import { cn } from "@/lib/cn";

export type CheckState = "ok" | "warn" | "bad";

export type OverviewCheck = {
  id: string;
  state: CheckState;
  title: string;
  detail?: string;
  /** Settings section that fixes it ("Beheben" button). */
  fixSection?: string;
};

export type NextStep = {
  id: string;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  onSelect: () => void;
};

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

const STATE_STYLE: Record<CheckState, string> = {
  ok: "bg-green/15 text-green",
  warn: "bg-escalation/15 text-escalation",
  bad: "bg-danger/15 text-danger",
};

function StateMark({ state }: { state: CheckState }) {
  return (
    <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg", STATE_STYLE[state])}>
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {state === "ok" ? (
          <path d="m5 12.5 4.5 4.5L19 7.5" />
        ) : state === "bad" ? (
          <path d="M6 6l12 12M18 6 6 18" />
        ) : (
          <path d="M12 7v6M12 16.5h.01" />
        )}
      </svg>
    </span>
  );
}

/** Which settings section a backend self-check problem belongs to. */
function problemSection(backend: CryptoBackend, problem: string): string {
  if (/CertPath|PrivatePath|certificate directory|private key directory/i.test(problem)) return "store";
  if (backend === "pgp" && /homedir|keyring/i.test(problem)) return "env";
  return "env";
}

/**
 * Overview tab: the backend's master switch, a readiness checklist (program,
 * key stores, plus page-specific checks such as keys and passphrases) with
 * "fix" jumps into the settings, the environment and next steps.
 */
export function CryptoOverview({
  backend,
  name,
  checks,
  nextSteps,
  onFix,
}: {
  backend: CryptoBackend;
  name: string;
  checks: OverviewCheck[];
  nextSteps: NextStep[];
  onFix: (section: string) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const settingsQ = useCryptoSettings();
  const statusQ = useQuery({
    queryKey: CRYPTO_STATUS_KEY,
    queryFn: ({ signal }) => api.adminCrypto.status(signal),
    staleTime: 60 * 1000,
  });
  const [error, setError] = useState<string | null>(null);
  const status = statusQ.data?.find((s) => s.backend === backend);
  const enabledField = settingsQ.data?.[backend].find((f) => f.name === `${backend}.enabled`);
  const enabled = enabledField?.value === true;
  const usable = status?.available ?? false;

  const toggleM = useMutation({
    mutationFn: (value: boolean) => api.adminCrypto.settingsUpdate({ [`${backend}.enabled`]: value }),
    onSuccess: async (data) => {
      setError(null);
      qc.setQueryData(CRYPTO_SETTINGS_KEY, data);
      await qc.invalidateQueries({ queryKey: CRYPTO_STATUS_KEY });
    },
    onError: (err) => setError(errText(err)),
  });

  // Known self-check messages in the UI language; anything else verbatim.
  const problemText = (problem: string): string => {
    const notConfigured = /^(.*?) is not configured$/.exec(problem);
    if (notConfigured) return t("admin.cryptoPage.problemNotConfigured", { name: notConfigured[1] });
    const notWritable = /^(.*?) (\S+) is not writable$/.exec(problem);
    if (notWritable) return t("admin.cryptoPage.problemNotWritable", { path: notWritable[2] });
    const cannotCreate = /^(.*?) (\S+) does not exist and cannot be created$/.exec(problem);
    if (cannotCreate) return t("admin.cryptoPage.problemCannotCreate", { path: cannotCreate[2] });
    return problem;
  };
  const all: OverviewCheck[] = [];
  if (status) {
    all.push({
      id: "binary",
      state: status.binary.available ? "ok" : "bad",
      title: status.binary.available
        ? t("admin.cryptoPage.checkBinaryOk")
        : t("admin.cryptoPage.checkBinaryMissing"),
      detail: status.binary.version || status.binary.reason || status.binary.path,
      fixSection: status.binary.available ? undefined : "env",
    });
    for (const problem of status.problems) {
      if (!status.binary.available && /^(gpg|openssl):/.test(problem)) continue;
      all.push({
        id: `problem-${problem}`,
        state: "bad",
        title: problemText(problem),
        detail: problemText(problem) === problem ? undefined : problem,
        fixSection: problemSection(backend, problem),
      });
    }
  }
  all.push(...checks);
  const blocking = all.some((c) => c.state === "bad");

  let headline: string;
  let sub: string;
  if (enabled) {
    headline = t("admin.cryptoPage.isOn", { name });
    sub = t("admin.cryptoPage.isOnHint");
  } else {
    headline = t("admin.cryptoPage.isOff", { name });
    sub = blocking ? t("admin.cryptoPage.isOffBlocked") : t("admin.cryptoPage.isOffHint");
  }
  const lockedNote: ReactNode =
    enabledField?.locked === true ? (
      <p className="flex items-center gap-1 text-xs text-escalation">
        <LockIcon className="h-3.5 w-3.5" />
        {enabledField.source === "env"
          ? t("admin.cryptoSettings.source.env", { name: enabledField.env_var ?? "" })
          : t("admin.cryptoSettings.source.znuny", { name: enabledField.znuny_setting ?? "" })}
      </p>
    ) : null;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] lg:items-start" data-testid={`crypto-overview-${backend}`}>
      <section className="space-y-4 rounded-xl border border-hairline bg-surface p-5">
        <div className="flex flex-wrap items-center gap-4">
          <Switch
            size="lg"
            checked={enabled}
            disabled={
              !enabledField || enabledField.locked || toggleM.isPending || (!enabled && !usable)
            }
            onChange={(v) => toggleM.mutate(v)}
            testId={`crypto-master-${backend}`}
            aria-label={t("admin.cryptoSettings.fields." + backend + ".enabled.label")}
          />
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-ink" data-testid={`crypto-headline-${backend}`}>
              {headline}
            </h2>
            <p className="text-sm text-muted">{sub}</p>
            {lockedNote}
            {error ? <p className="text-xs text-danger">{error}</p> : null}
          </div>
        </div>
        <ul className="divide-y divide-hairline border-t border-hairline" data-testid={`crypto-checks-${backend}`}>
          {all.map((c) => (
            <li key={c.id} className="flex items-center gap-3 py-2.5" data-state={c.state}>
              <StateMark state={c.state} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-ink">{c.title}</div>
                {c.detail ? <div className="break-all text-xs text-muted">{c.detail}</div> : null}
              </div>
              {c.state !== "ok" && c.fixSection ? (
                <Button size="sm" onClick={() => onFix(c.fixSection ?? "general")} data-testid={`crypto-fix-${c.id}`}>
                  {t("admin.cryptoPage.fix")}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
      <div className="grid gap-4">
        {status ? (
          <section className="space-y-2 rounded-xl border border-hairline bg-surface p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <ServerIcon className="h-4 w-4 text-accent" />
              {t("admin.cryptoPage.environment")}
            </h3>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-muted">{t("admin.cryptoPage.program")}</dt>
              <dd className="break-all font-mono text-xs text-ink">{status.binary.version || status.binary.path || "—"}</dd>
              {Object.entries(status.paths).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted">{t(`admin.crypto.path.${k}`, { defaultValue: k })}</dt>
                  <dd className={cn("break-all font-mono text-xs", v ? "text-ink" : "text-danger")}>
                    {v || t("admin.cryptoPage.notSet")}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}
        {nextSteps.length > 0 ? (
          <section className="space-y-2 rounded-xl border border-hairline bg-surface p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <SparkIcon className="h-4 w-4 text-accent" />
              {t("admin.cryptoPage.nextSteps")}
            </h3>
            <div className="grid gap-1.5">
              {nextSteps.map((s) => {
                const Icon = s.icon;
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={s.onSelect}
                    data-testid={`crypto-next-${s.id}`}
                    className="flex items-center gap-2.5 rounded-lg bg-surface-subtle px-3 py-2 text-left text-sm text-ink hover:bg-hairline"
                  >
                    <Icon className="h-4 w-4 shrink-0 text-accent" />
                    <span className="min-w-0 flex-1">{s.label}</span>
                    <ChevronLeftIcon className="h-4 w-4 rotate-180 text-muted" />
                  </button>
                );
              })}
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}
