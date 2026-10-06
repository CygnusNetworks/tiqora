import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

/**
 * Table cells shared by the agent customer directory and the admin customer
 * users list: name first with the e-mail underneath, then the company with
 * its customer id. Both truncate instead of widening the table.
 */
export function CustomerNameCell({
  firstName,
  lastName,
  email,
  login,
  title,
}: {
  firstName: string;
  lastName: string;
  email: string;
  login: string;
  /** Optional wrapper for the name (e.g. a link to the customer centre). */
  title?: (name: ReactNode) => ReactNode;
}) {
  const full = [firstName, lastName].map((p) => p.trim()).filter(Boolean).join(" ") || login;
  const name = <span className="truncate font-medium text-ink">{full}</span>;
  return (
    <span className="flex min-w-0 max-w-[22rem] flex-col text-right md:text-left">
      {title ? title(name) : name}
      <span className="truncate text-xs text-muted">{email || login}</span>
    </span>
  );
}

export function CustomerCompanyCell({
  companyName,
  customerId,
}: {
  companyName: string | null | undefined;
  customerId: string;
}) {
  const { t } = useTranslation();
  return (
    <span className="flex min-w-0 max-w-[16rem] flex-col text-right md:text-left">
      <span className="truncate text-ink">{companyName || t("customerDirectory.noCompany")}</span>
      <span className="truncate font-mono text-[11px] text-muted">{customerId}</span>
    </span>
  );
}
