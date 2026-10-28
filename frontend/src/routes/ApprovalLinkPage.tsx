import { useTranslation } from "react-i18next";
import { logoUrl } from "@/lib/assets";

/** Placeholder of the public decision page (`/approve`); the card with inspect/decide follows in the link-page task. */
export function ApprovalLinkPage() {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg p-4">
      <div className="w-full max-w-md space-y-4 rounded-lg border border-hairline bg-surface p-6" data-testid="approval-link-page">
        <img src={logoUrl} alt="" width={32} height={32} className="rounded" />
        <h1 className="font-display text-xl font-semibold text-ink">{t("approvals.link.title")}</h1>
      </div>
    </div>
  );
}
