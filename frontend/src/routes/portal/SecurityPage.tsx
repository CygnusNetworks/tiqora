import { useTranslation } from "react-i18next";
import { TwoFactorPanel } from "@/components/security/TwoFactorPanel";
import { customerTwoFactorApi } from "@/components/security/twoFactorApi";

/** Customer portal account security: second factors (TOTP and passkeys). */
export function SecurityPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto w-full max-w-lg" data-testid="portal-security-page">
      <h1 className="font-display text-xl font-bold tracking-tight text-ink">
        {t("portal.security.title")}
      </h1>
      <p className="mt-1.5 text-sm text-muted">{t("portal.security.intro")}</p>
      <TwoFactorPanel api={customerTwoFactorApi} testIdPrefix="portal-security" />
    </div>
  );
}
