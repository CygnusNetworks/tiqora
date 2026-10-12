import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { copyText } from "@/components/admin/identity/copyText";
import { Button } from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Switch } from "@/components/ui/Switch";
import { webchatApi, type ChatConfig } from "@/lib/webchatApi";
import { setValues } from "@/lib/webchatForm";
import { IdentitySecretSection } from "./IdentitySecretSection";
import type { TabProps } from "./shared";

/** The embed code of a saved chat, a hint for the website's CSP and key rotation. */
export function EmbedTab({
  config,
  onRotated,
  draft,
  errors,
  onDraft,
}: TabProps & {
  config: ChatConfig | null;
  /** The saved config changed on the server (new key, secret created or removed). */
  onRotated: (next: ChatConfig) => void;
}) {
  const { t } = useTranslation();
  const { confirm, dialog } = useConfirm();
  const [copied, setCopied] = useState<"ok" | "failed" | null>(null);
  const codeRef = useRef<HTMLElement>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const rotateM = useMutation({
    mutationFn: (id: number) => webchatApi.rotateKey(id),
    onSuccess: onRotated,
  });

  if (!config) {
    return (
      <p data-testid="webchat-embed-unsaved" className="rounded-lg border border-dashed border-hairline p-4 text-sm text-muted">
        {t("webchat.admin.embed.afterSave")}
      </p>
    );
  }

  const snippet = config.embed_snippet;
  const base = /\ssrc="([^"]*)"/.exec(snippet)?.[1];
  let origin = window.location.origin;
  try {
    if (base) origin = new URL(base).origin;
  } catch {
    // relative src: keep this page's origin
  }
  const copy = async () => {
    const ok = await copyText(snippet);
    if (!ok && codeRef.current) {
      // Last resort: select the text so Ctrl/Cmd+C works.
      const range = document.createRange();
      range.selectNodeContents(codeRef.current);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
    }
    setCopied(ok ? "ok" : "failed");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(null), 3000);
  };

  const rotate = async () => {
    const ok = await confirm({
      title: t("webchat.admin.embed.rotate"),
      message: t("webchat.admin.embed.rotateConfirm"),
      confirmLabel: t("webchat.admin.embed.rotate"),
      variant: "danger",
    });
    if (ok) rotateM.mutate(config.id);
  };

  return (
    <div className="space-y-5" data-testid="webchat-tab-embed">
      <section className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.embed.code")}</h3>
          <div className="flex items-center gap-2">
            <span role="status" data-testid="webchat-embed-copied" className="text-xs text-muted">
              {copied === "ok" ? t("webchat.admin.embed.copied") : copied === "failed" ? t("webchat.admin.embed.copyFailed") : ""}
            </span>
            <Button size="sm" data-testid="webchat-embed-copy" onClick={() => void copy()}>
              {t("webchat.admin.embed.copy")}
            </Button>
          </div>
        </div>
        <pre className="overflow-x-auto rounded-md border border-hairline bg-surface-subtle p-3 text-xs text-ink">
          <code ref={codeRef} data-testid="webchat-embed-snippet" className="whitespace-pre-wrap break-all font-mono">
            {snippet}
          </code>
        </pre>
        <p className="text-xs text-muted">{t("webchat.admin.embed.where")}</p>
      </section>

      <section className="space-y-1">
        <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.embed.csp")}</h3>
        <p className="text-xs text-muted">
          {t("webchat.admin.embed.cspHint")} <code className="font-mono text-ink">{origin}</code>
        </p>
        <pre className="overflow-x-auto rounded-md border border-hairline bg-surface-subtle p-3 text-xs text-ink">
          <code data-testid="webchat-embed-csp" className="font-mono">{`script-src ${origin}; frame-src ${origin};`}</code>
        </pre>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.embed.rotate")}</h3>
        <p className="text-xs text-muted">{t("webchat.admin.embed.rotateHint")}</p>
        <Button variant="danger" size="sm" data-testid="webchat-embed-rotate" disabled={rotateM.isPending} onClick={() => void rotate()}>
          {t("webchat.admin.embed.rotate")}
        </Button>
        {rotateM.isError && (
          <p role="alert" className="text-xs text-danger">
            {t("webchat.admin.saveFailed")}
          </p>
        )}
      </section>

      <section className="space-y-3" data-testid="webchat-portal-section">
        <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.portal.title")}</h3>
        <div className="flex items-start gap-3">
          <Switch
            checked={draft.values.show_on_portal}
            onChange={(show_on_portal) => onDraft(setValues(draft, { show_on_portal }))}
            testId="webchat-show-on-portal"
            aria-labelledby="webchat-show-on-portal-label"
            aria-describedby="webchat-show-on-portal-hint"
          />
          <div>
            <span id="webchat-show-on-portal-label" className="text-sm text-ink">
              {t("webchat.admin.portal.show")}
            </span>
            <p id="webchat-show-on-portal-hint" className="text-xs text-muted">
              {t("webchat.admin.portal.showHint")}
            </p>
            {errors.show_on_portal && (
              <p role="alert" data-testid="webchat-show-on-portal-error" className="mt-1 text-xs text-danger">
                {errors.show_on_portal}
              </p>
            )}
          </div>
        </div>
      </section>

      <IdentitySecretSection
        configId={config.id}
        isSet={config.identity_secret_set}
        onChanged={(isSet) => onRotated({ ...config, identity_secret_set: isSet })}
      />
      <div className="flex items-start gap-3" data-testid="webchat-create-customers-row">
        <Switch
          checked={draft.values.identity_create_customers}
          onChange={(identity_create_customers) => onDraft(setValues(draft, { identity_create_customers }))}
          testId="webchat-create-customers"
          aria-labelledby="webchat-create-customers-label"
          aria-describedby="webchat-create-customers-hint"
        />
        <div>
          <span id="webchat-create-customers-label" className="text-sm text-ink">
            {t("webchat.admin.identity.createCustomers")}
          </span>
          <p id="webchat-create-customers-hint" className="text-xs text-muted">
            {t("webchat.admin.identity.createCustomersHint")}
          </p>
        </div>
      </div>
      {dialog}
    </div>
  );
}
