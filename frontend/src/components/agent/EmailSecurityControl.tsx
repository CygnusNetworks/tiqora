import type { ComponentType, SVGProps } from "react";
import { useTranslation } from "react-i18next";
import { SelectField } from "@/components/ui/SelectField";
import { BanIcon, LockIcon, PencilIcon, ShieldIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import {
  signs,
  encrypts,
  offeredModes,
  pickableBackends,
  type EmailSecurity,
  type EmailSecurityState,
  type SecurityMode,
} from "./useEmailSecurity";

type Backend = EmailSecurityState["backend"];

const MODE_ICON: Record<SecurityMode, ComponentType<SVGProps<SVGSVGElement>>> = {
  none: BanIcon,
  sign: PencilIcon,
  encrypt: LockIcon,
  sign_encrypt: ShieldIcon,
};

/** One segmented button group (modes, backends). */
function Segmented<T extends string>({
  items,
  value,
  onChange,
  testId,
  label,
}: {
  items: { value: T; label: string; icon?: ComponentType<SVGProps<SVGSVGElement>> }[];
  value: T;
  onChange: (v: T) => void;
  testId: string;
  label: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      data-testid={testId}
      className="inline-flex flex-wrap gap-0.5 rounded-md border border-hairline bg-surface-subtle p-0.5"
    >
      {items.map((item) => {
        const on = item.value === value;
        const IconCmp = item.icon;
        return (
          <button
            key={item.value}
            type="button"
            role="radio"
            aria-checked={on}
            data-testid={`${testId}-${item.value}`}
            onClick={() => onChange(item.value)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs",
              on
                ? "bg-surface font-semibold text-ink shadow-sm ring-1 ring-hairline"
                : "text-muted hover:text-ink",
            )}
          >
            {IconCmp ? <IconCmp className={cn("h-3.5 w-3.5", on && "text-accent")} /> : null}
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

/** The compose "Sicherheit" row: the modes the queue allows (icons), the
 * backend only when PGP and S/MIME are both enabled and set up, method, sign
 * key, and the recipients' key status when encrypting. Renders nothing when
 * the queue offers nothing but a plain mail. */
export function EmailSecurityControl({
  security,
  testId = "email-security",
}: {
  security: EmailSecurity;
  testId?: string;
}) {
  const { t } = useTranslation();
  const { options, state, setState, backendOpts, blocked } = security;
  const modes = offeredModes(options);
  if (!options || modes.length === 0 || !state) return null;
  const update = (patch: Partial<EmailSecurityState>) =>
    setState({ ...state, ...patch });
  const backends = pickableBackends(options);
  const signKeys = backendOpts?.sign_keys ?? [];
  const queueSign = options.queue_sign ?? null;
  const onBackend = (b: Backend) => {
    const next = (options.backends ?? []).find((x) => x.backend === b);
    const firstKey = (next?.sign_keys ?? []).find((k) => k.usable)?.key ?? "";
    update({
      backend: b,
      method: "detached",
      signKey: queueSign?.backend === b ? (queueSign.sign_key ?? firstKey) : firstKey,
    });
  };

  return (
    <div className="space-y-1.5 text-xs" data-testid={testId}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1 text-muted">
          <ShieldIcon className="h-3.5 w-3.5" />
          {t("ticket.emailSecurity.label")}
        </span>
        <Segmented
          items={modes.map((m) => ({
            value: m,
            label: t(`ticket.emailSecurity.mode.${m}`),
            icon: MODE_ICON[m],
          }))}
          value={state.mode}
          onChange={(m) => {
            const patch: Partial<EmailSecurityState> = { mode: m };
            if (signs(m) && !state.signKey) {
              patch.signKey = signKeys.find((k) => k.usable)?.key ?? "";
            }
            update(patch);
          }}
          testId={`${testId}-mode`}
          label={t("ticket.emailSecurity.label")}
        />
        {state.mode !== "none" && backends.length > 1 && (
          <Segmented
            items={backends.map((b) => ({
              value: b.backend,
              label: b.backend === "smime" ? "S/MIME" : "PGP",
            }))}
            value={state.backend}
            onChange={onBackend}
            testId={`${testId}-backend`}
            label={t("ticket.emailSecurity.backend")}
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
                "inline-flex items-center gap-1 rounded px-1.5 py-0.5",
                r.status === "ok"
                  ? "bg-success/15 text-success"
                  : "bg-danger/15 text-danger",
              )}
            >
              {r.status === "ok" ? (
                <LockIcon className="h-3 w-3" />
              ) : (
                <BanIcon className="h-3 w-3" />
              )}
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
      {options.encrypt_policy === "required" && (
        <p className="inline-flex items-center gap-1 text-muted" data-testid={`${testId}-required`}>
          <LockIcon className="h-3.5 w-3.5" />
          {t("ticket.emailSecurity.required")}
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
