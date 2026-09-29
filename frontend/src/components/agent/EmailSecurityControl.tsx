import { useTranslation } from "react-i18next";
import { SelectField } from "@/components/ui/SelectField";
import { cn } from "@/lib/cn";
import {
  signs,
  encrypts,
  type EmailSecurity,
  type EmailSecurityState,
  type SecurityMode,
} from "./useEmailSecurity";

type Backend = EmailSecurityState["backend"];

const MODES: SecurityMode[] = ["none", "sign", "encrypt", "sign_encrypt"];

/** The compose "Sicherheit" row: mode, backend/method, sign key, and the
 * recipients' key status when encrypting. Renders nothing when neither PGP
 * nor S/MIME is enabled. */
export function EmailSecurityControl({
  security,
  testId = "email-security",
}: {
  security: EmailSecurity;
  testId?: string;
}) {
  const { t } = useTranslation();
  const { options, state, setState, backendOpts, blocked } = security;
  if (!options?.enabled || !state) return null;
  const update = (patch: Partial<EmailSecurityState>) =>
    setState({ ...state, ...patch });
  const backends = options.backends ?? [];
  const signKeys = backendOpts?.sign_keys ?? [];

  const onBackend = (b: Backend) => {
    const next = backends.find((x) => x.backend === b);
    const firstKey = (next?.sign_keys ?? []).find((k) => k.usable)?.key ?? "";
    update({
      backend: b,
      method: "detached",
      signKey: options.default?.backend === b ? (options.default.sign_key ?? firstKey) : firstKey,
    });
  };

  return (
    <div className="space-y-1 text-xs" data-testid={testId}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted">{t("ticket.emailSecurity.label")}</span>
        <SelectField
          items={MODES.map((m) => ({ value: m, label: t(`ticket.emailSecurity.mode.${m}`) }))}
          value={state.mode}
          onChange={(m) => {
            const patch: Partial<EmailSecurityState> = { mode: m };
            if (signs(m) && !state.signKey) {
              patch.signKey = signKeys.find((k) => k.usable)?.key ?? "";
            }
            update(patch);
          }}
          testId={`${testId}-mode`}
          className="w-auto min-w-40"
          aria-label={t("ticket.emailSecurity.label")}
        />
        {state.mode !== "none" && backends.length > 1 && (
          <SelectField
            items={backends.map((b) => ({
              value: b.backend,
              label: b.backend === "smime" ? "S/MIME" : "PGP",
            }))}
            value={state.backend}
            onChange={onBackend}
            testId={`${testId}-backend`}
            className="w-auto"
            aria-label={t("ticket.emailSecurity.backend")}
          />
        )}
        {state.mode !== "none" && state.backend === "pgp" && (
          <SelectField
            items={(backendOpts?.methods ?? ["detached"]).map((m) => ({
              value: m,
              label: t(`ticket.emailSecurity.method.${m}`),
            }))}
            value={state.method}
            onChange={(m) => update({ method: m })}
            testId={`${testId}-method`}
            className="w-auto"
            aria-label={t("ticket.emailSecurity.methodLabel")}
          />
        )}
        {signs(state.mode) && (
          <SelectField
            items={signKeys.map((k) => ({
              value: k.key,
              label: k.label,
              hint: k.usable ? undefined : t(`ticket.emailSecurity.keyStatus.${k.status}`, { defaultValue: k.status }),
            }))}
            value={state.signKey || null}
            onChange={(k) => update({ signKey: k })}
            placeholder={t("ticket.emailSecurity.signKey")}
            testId={`${testId}-sign-key`}
            className="w-auto max-w-72"
            aria-label={t("ticket.emailSecurity.signKey")}
          />
        )}
      </div>
      {encrypts(state.mode) && backendOpts && (backendOpts.recipients ?? []).length > 0 && (
        <ul className="flex flex-wrap gap-1" data-testid={`${testId}-recipients`}>
          {(backendOpts.recipients ?? []).map((r) => (
            <li
              key={r.address}
              data-status={r.status}
              className={cn(
                "rounded px-1.5 py-0.5",
                r.status === "ok"
                  ? "bg-success/15 text-success"
                  : "bg-danger/15 text-danger",
              )}
            >
              {r.status === "ok" ? "🔒 " : "⚠ "}
              {r.address}
              {r.status !== "ok" && ` — ${t(`ticket.emailSecurity.keyStatus.${r.status}`, { defaultValue: r.status })}`}
            </li>
          ))}
        </ul>
      )}
      {(options.warnings ?? []).length > 0 && state.mode !== "none" && (
        <p className="text-escalation" data-testid={`${testId}-warnings`}>
          {(options.warnings ?? []).join(" · ")}
        </p>
      )}
      {blocked && (
        <p className="text-danger" data-testid={`${testId}-blocked`}>
          {blocked}
        </p>
      )}
    </div>
  );
}
