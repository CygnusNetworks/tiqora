import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  identityApi,
  providerFieldErrors,
  type ProviderAudience,
  type ScimToken,
  type ScimTokenCreate,
} from "@/lib/identityApi";
import { CopyButton } from "./CopyButton";

type Kind = ScimTokenCreate["user_kind"];

const DEFAULT_DAYS = 365;
const MAX_DAYS = 730;

function kindsForAudience(audience: ProviderAudience): Kind[] {
  return audience === "both" ? ["agent", "customer"] : [audience];
}

/**
 * Creates a SCIM key. The raw token lives only in this component's state and
 * is dropped the moment the dialog closes: the server never shows it again.
 */
export function ScimTokenDialog({
  open,
  providerId,
  audience,
  onClose,
  onCreated,
}: {
  open: boolean;
  providerId: number;
  audience: ProviderAudience;
  onClose: () => void;
  onCreated: (record: ScimToken) => void;
}) {
  const { t } = useTranslation();
  const kinds = kindsForAudience(audience);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Kind>(kinds[0]);
  const [days, setDays] = useState(String(DEFAULT_DAYS));
  const [token, setToken] = useState<string | null>(null);

  const daysNum = Number(days);
  const daysValid = Number.isInteger(daysNum) && daysNum >= 1 && daysNum <= MAX_DAYS;
  const nameValid = name.trim().length > 0;

  const createM = useMutation({
    // The raw token must not outlive the dialog in the MutationCache.
    gcTime: 0,
    mutationFn: () =>
      identityApi.createScimToken(providerId, { name: name.trim(), user_kind: kind, expires_in_days: daysNum }),
    onSuccess: (res) => {
      setToken(res.token);
      onCreated(res.record);
    },
  });

  const close = () => {
    setToken(null);
    setName("");
    setDays(String(DEFAULT_DAYS));
    setKind(kinds[0]);
    createM.reset();
    onClose();
  };

  const serverErrors = providerFieldErrors(createM.error).common;
  const hasFieldErrors = Object.keys(serverErrors).length > 0;

  return (
    <Dialog
      open={open}
      onClose={close}
      // While the one-time token is shown, Escape or a stray backdrop click must not discard it.
      dismissible={!token}
      title={t(token ? "admin.identity.scim.tokens.createdTitle" : "admin.identity.scim.tokens.create")}
      footer={
        token ? (
          <Button type="button" variant="primary" data-testid="scim-token-close" onClick={close}>
            {t("admin.identity.scim.tokens.done")}
          </Button>
        ) : (
          <>
            <Button type="button" onClick={close}>
              {t("admin.identity.scim.tokens.cancel")}
            </Button>
            <Button
              type="button"
              variant="primary"
              data-testid="scim-token-submit"
              disabled={!nameValid || !daysValid || createM.isPending}
              onClick={() => createM.mutate()}
            >
              {t("admin.identity.scim.tokens.create")}
            </Button>
          </>
        )
      }
    >
      {token ? (
        <div className="space-y-3" data-testid="scim-token-result">
          <p className="text-sm text-ink">{t("admin.identity.scim.tokens.copyHint")}</p>
          <code
            data-testid="scim-token-value"
            className="block break-all rounded border border-hairline bg-surface-subtle px-2 py-2 font-mono text-[12.5px] text-ink"
          >
            {token}
          </code>
          <CopyButton text={token} testId="scim-token-copy" />
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <label htmlFor="scim-token-name" className="mb-1 block text-xs font-medium text-muted">
              {t("admin.identity.scim.tokens.name")}
            </label>
            <input
              id="scim-token-name"
              data-testid="scim-token-name"
              type="text"
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
          <div>
            <label htmlFor="scim-token-kind" className="mb-1 block text-xs font-medium text-muted">
              {t("admin.identity.scim.tokens.userKind")}
            </label>
            <SelectField
              id="scim-token-kind"
              testId="scim-token-kind"
              items={kinds.map((k) => ({ value: k, label: t(`admin.identity.scim.kinds.${k}`) }))}
              value={kind}
              onChange={setKind}
            />
          </div>
          <div>
            <label htmlFor="scim-token-days" className="mb-1 block text-xs font-medium text-muted">
              {t("admin.identity.scim.tokens.expiresInDays")}
            </label>
            <input
              id="scim-token-days"
              data-testid="scim-token-days"
              type="number"
              min={1}
              max={MAX_DAYS}
              value={days}
              onChange={(e) => setDays(e.target.value)}
              className="w-32 rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent"
            />
            {!daysValid && (
              <p role="alert" data-testid="scim-token-days-error" className="mt-1 text-xs text-danger">
                {t("admin.identity.scim.tokens.daysRange", { max: MAX_DAYS })}
              </p>
            )}
          </div>
          {createM.isError && (
            <p role="alert" data-testid="scim-token-error" className="text-sm text-danger">
              {hasFieldErrors
                ? Object.values(serverErrors).join(" ")
                : apiErrorMessage(createM.error, t("admin.identity.scim.tokens.createError"))}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}
