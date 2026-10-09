import { useTranslation } from "react-i18next";
import { TwoFactorPanel } from "@/components/security/TwoFactorPanel";
import { agentTwoFactorApi } from "@/components/security/twoFactorApi";

/** Agent account security: second factors (TOTP and passkeys). */
export function SecurityPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto w-full max-w-lg px-4 py-8">
      <h1 className="font-display text-xl font-bold tracking-tight text-ink">
        {t("security.title")}
      </h1>
      <TwoFactorPanel api={agentTwoFactorApi} testIdPrefix="security" />
    </div>
  );
}
