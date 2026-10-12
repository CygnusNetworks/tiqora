import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { copyText } from "@/components/admin/identity/copyText";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { webchatApi } from "@/lib/webchatApi";
import { inputClass } from "./shared";
import { IDENTITY_SAMPLES, type Lang } from "./identitySamples";

const CALL_SAMPLE = `Tiqora.chat.identify("<signed sign-in>");`;

/** Create, replace or remove the secret that signs visitors in from the customer's own website. */
export function IdentitySecretSection({
  configId,
  isSet,
  onChanged,
}: {
  configId: number;
  isSet: boolean;
  /** The server now has (`true`) or no longer has (`false`) a secret. */
  onChanged: (isSet: boolean) => void;
}) {
  const { t } = useTranslation();
  const { confirm, dialog } = useConfirm();
  const [secret, setSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState<"ok" | "failed" | null>(null);
  const [lang, setLang] = useState<Lang>("node");
  const inputRef = useRef<HTMLInputElement>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const createM = useMutation({
    mutationFn: () => webchatApi.createIdentitySecret(configId),
    onSuccess: (r) => {
      setSecret(r.secret);
      setCopied(null);
      onChanged(true);
    },
  });
  const deleteM = useMutation({
    mutationFn: () => webchatApi.deleteIdentitySecret(configId),
    onSuccess: () => {
      setSecret(null);
      onChanged(false);
    },
  });

  const create = () => createM.mutate();
  const regenerate = async () => {
    const ok = await confirm({
      title: t("webchat.admin.identity.regenerate"),
      message: t("webchat.admin.identity.regenerateConfirm"),
      confirmLabel: t("webchat.admin.identity.regenerate"),
      variant: "danger",
    });
    if (ok) create();
  };
  const remove = async () => {
    const ok = await confirm({
      title: t("webchat.admin.identity.remove"),
      message: t("webchat.admin.identity.removeConfirm"),
      confirmLabel: t("webchat.admin.identity.remove"),
      variant: "danger",
    });
    if (ok) deleteM.mutate();
  };
  const copy = async () => {
    if (!secret) return;
    const ok = await copyText(secret);
    if (!ok) inputRef.current?.select();
    setCopied(ok ? "ok" : "failed");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(null), 3000);
  };

  const busy = createM.isPending || deleteM.isPending;
  return (
    <section className="space-y-3" data-testid="webchat-identity">
      <div>
        <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.identity.title")}</h3>
        <p className="mt-1 text-xs text-muted">{t("webchat.admin.identity.intro")}</p>
      </div>

      <div className="space-y-2 rounded-lg border border-hairline p-3">
        <h4 className="text-xs font-medium text-muted">{t("webchat.admin.identity.keyTitle")}</h4>
        {secret ? (
          <div className="space-y-2" data-testid="webchat-identity-secret-box">
            <p role="alert" className="text-xs font-semibold text-escalation" data-testid="webchat-identity-warning">
              {t("webchat.admin.identity.shownOnce")}
            </p>
            <p className="text-xs text-muted">{t("webchat.admin.identity.shownOnceHint")}</p>
            <div className="flex items-center gap-2">
              <input
                ref={inputRef}
                readOnly
                aria-label={t("webchat.admin.identity.secretLabel")}
                data-testid="webchat-identity-secret"
                className={`${inputClass} font-mono`}
                value={secret}
                onFocus={(e) => e.currentTarget.select()}
              />
              <Button size="sm" data-testid="webchat-identity-copy" onClick={() => void copy()}>
                {t("webchat.admin.identity.copy")}
              </Button>
            </div>
            <span role="status" data-testid="webchat-identity-copied" className="block text-xs text-muted">
              {copied === "ok"
                ? t("webchat.admin.identity.copied")
                : copied === "failed"
                  ? t("webchat.admin.identity.copyFailed")
                  : ""}
            </span>
          </div>
        ) : (
          <p className="text-xs text-muted" data-testid="webchat-identity-state">
            {isSet ? t("webchat.admin.identity.keySet") : t("webchat.admin.identity.keyNone")}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          {isSet ? (
            <>
              <Button size="sm" data-testid="webchat-identity-regenerate" disabled={busy} onClick={() => void regenerate()}>
                {t("webchat.admin.identity.regenerate")}
              </Button>
              <Button variant="danger" size="sm" data-testid="webchat-identity-remove" disabled={busy} onClick={() => void remove()}>
                {t("webchat.admin.identity.remove")}
              </Button>
            </>
          ) : (
            <Button size="sm" data-testid="webchat-identity-create" disabled={busy} onClick={create}>
              {t("webchat.admin.identity.create")}
            </Button>
          )}
        </div>
        {(createM.isError || deleteM.isError) && (
          <p role="alert" className="text-xs text-danger" data-testid="webchat-identity-error">
            {t("webchat.admin.identity.failed")}
          </p>
        )}
        <p className="text-xs text-muted">{t("webchat.admin.identity.warning")}</p>
      </div>

      <div className="space-y-2">
        <h4 className="text-xs font-medium text-muted">{t("webchat.admin.identity.samplesTitle")}</h4>
        <p className="text-xs text-muted">{t("webchat.admin.identity.samplesHint")}</p>
        <SegmentedControl
          testId="webchat-identity-lang"
          aria-label={t("webchat.admin.identity.sampleTabs")}
          items={[
            { value: "node", label: "Node.js" },
            { value: "php", label: "PHP" },
            { value: "python", label: "Python" },
          ]}
          value={lang}
          onChange={(l) => setLang(l as Lang)}
        />
        <pre className="overflow-x-auto rounded-md border border-hairline bg-surface-subtle p-3 text-xs text-ink">
          <code data-testid="webchat-identity-sample" className="font-mono">
            {IDENTITY_SAMPLES[lang]}
          </code>
        </pre>
        <p className="text-xs text-muted">{t("webchat.admin.identity.samplesCall")}</p>
        <pre className="overflow-x-auto rounded-md border border-hairline bg-surface-subtle p-3 text-xs text-ink">
          <code className="font-mono">{CALL_SAMPLE}</code>
        </pre>
      </div>
      {dialog}
    </section>
  );
}
