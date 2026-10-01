import type { ComponentType, SVGProps } from "react";
import { useTranslation } from "react-i18next";
import { SelectField } from "@/components/ui/SelectField";
import { BanIcon, KeyIcon, LockIcon, PencilIcon, ShieldIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import type { CryptoComposeKeyOut } from "@/lib/api";
import {
  signs,
  encrypts,
  backendUsable,
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

/** One segmented button group (modes, backends). `bare` drops the frame so
 * the group can sit inside the key rail. An item with `disabled` stays
 * visible but cannot be picked; `reason` says why, next to the label. */
function Segmented<T extends string>({
  items,
  value,
  onChange,
  testId,
  label,
  bare,
}: {
  items: {
    value: T;
    label: string;
    icon?: ComponentType<SVGProps<SVGSVGElement>>;
    disabled?: boolean;
    reason?: string;
  }[];
  value: T;
  onChange: (v: T) => void;
  testId: string;
  label: string;
  bare?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      data-testid={testId}
      className={cn(
        "inline-flex flex-wrap items-center gap-0.5 p-0.5",
        bare ? "bg-transparent" : "rounded-md border border-hairline bg-surface-subtle",
      )}
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
            aria-disabled={item.disabled || undefined}
            title={item.disabled ? item.reason : undefined}
            data-testid={`${testId}-${item.value}`}
            onClick={() => {
              if (!item.disabled) onChange(item.value);
            }}
            className={cn(
              "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors duration-100 motion-reduce:transition-none",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
              item.disabled
                ? "cursor-not-allowed text-muted opacity-60"
                : on
                  ? "bg-surface text-ink shadow-sm ring-1 ring-hairline"
                  : "text-muted hover:text-ink",
            )}
          >
            {IconCmp ? <IconCmp className={cn("h-3.5 w-3.5", on && !item.disabled && "text-accent")} /> : null}
            {item.label}
            {item.disabled && item.reason ? (
              <span className="text-[11px] font-normal text-escalation">{item.reason}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** A sign key as "<id> <address>": the key id plus the queue's sender address
 * when the key carries it (else its first one) — no user-id name, no dates. */
function signKeyLabel(k: CryptoComposeKeyOut, from: string | null | undefined): string {
  const emails = k.emails ?? [];
  const email = from && emails.includes(from) ? from : emails[0];
  return email ? `${k.key} ${email}` : k.label;
}

/** The compose "Sicherheit" row: the modes the queue allows (icons), the
 * backend only when PGP and S/MIME are both enabled and set up, the PGP method
 * (only when the queue's sign key does not fix it already), sign key, and the recipients' key status when encrypting. Renders nothing when
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
  const backendList = options.backends ?? [];
  const usableFor = (b: Backend, mode: SecurityMode) =>
    backendUsable(
      backendList.find((x) => x.backend === b),
      mode,
    );
  const signKeys = backendOpts?.sign_keys ?? [];
  const queueSign = options.queue_sign ?? null;
  // The queue's sign key (PGP::Detached::<id> / PGP::Inline::<id>) already
  // fixes the PGP method; the agent only picks one when the queue has none.
  const queueMethod = queueSign?.backend === "pgp" ? queueSign.method : null;
  const methodFor = (b: Backend) => (b === "pgp" && queueMethod) || "detached";
  const onBackend = (b: Backend) => {
    const next = (options.backends ?? []).find((x) => x.backend === b);
    const firstKey = (next?.sign_keys ?? []).find((k) => k.usable)?.key ?? "";
    update({
      backend: b,
      method: methodFor(b),
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
            // The chosen backend may have no sign key for a signing mode:
            // move to one that has.
            let be = backendList.find((x) => x.backend === state.backend);
            if (m !== "none" && !usableFor(state.backend, m)) {
              const alt = backendList.find((x) => backendUsable(x, m));
              if (alt) {
                be = alt;
                patch.backend = alt.backend;
                patch.method = methodFor(alt.backend);
                patch.signKey = "";
              }
            }
            if (signs(m) && !(patch.signKey ?? state.signKey)) {
              patch.signKey = (be?.sign_keys ?? []).find((k) => k.usable)?.key ?? "";
            }
            update(patch);
          }}
          testId={`${testId}-mode`}
          label={t("ticket.emailSecurity.label")}
        />
      </div>
      {state.mode !== "none" && (
        <div
          className="flex w-fit max-w-full flex-wrap items-stretch overflow-hidden rounded-md border border-hairline bg-surface"
          data-testid={`${testId}-rail`}
        >
          {backends.length > 1 && (
            <Segmented
              bare
              items={backends.map((b) => {
                const off = !backendUsable(b, state.mode);
                return {
                  value: b.backend,
                  label: b.backend === "smime" ? "S/MIME" : "PGP",
                  disabled: off,
                  reason: off ? t("ticket.emailSecurity.keyStatus.missing") : undefined,
                };
              })}
              value={state.backend}
              onChange={onBackend}
              testId={`${testId}-backend`}
              label={t("ticket.emailSecurity.backend")}
            />
          )}
          {state.backend === "pgp" && !queueMethod && (
            <div className="flex items-stretch border-l border-hairline first:border-l-0">
              <SelectField
                items={(backendOpts?.methods ?? ["detached"]).map((m) => ({
                  value: m,
                  label: t(`ticket.emailSecurity.method.${m}`),
                }))}
                value={state.method}
                onChange={(m) => update({ method: m })}
                testId={`${testId}-method`}
                bare
                className="w-auto"
                aria-label={t("ticket.emailSecurity.methodLabel")}
              />
            </div>
          )}
          {signs(state.mode) && (
            <div className="flex items-center border-l border-hairline pl-2.5 first:border-l-0 first:pl-0">
              <KeyIcon className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
              <SelectField
                items={signKeys.map((k) => ({
                  value: k.key,
                  label: signKeyLabel(k, options.from_address),
                  hint: k.usable ? undefined : t(`ticket.emailSecurity.keyStatus.${k.status}`, { defaultValue: k.status }),
                }))}
                value={state.signKey || null}
                onChange={(k) => update({ signKey: k })}
                placeholder={t("ticket.emailSecurity.signKey")}
                testId={`${testId}-sign-key`}
                bare
                className="w-auto max-w-72"
                aria-label={t("ticket.emailSecurity.signKey")}
              />
            </div>
          )}
        </div>
      )}
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
