import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";

const LINKS: { to: string; labelKey: string }[] = [
  { to: "/admin/users", labelKey: "admin.nav.users" },
  { to: "/admin/groups", labelKey: "admin.nav.groups" },
  { to: "/admin/roles", labelKey: "admin.nav.roles" },
  { to: "/admin/queues", labelKey: "admin.nav.queues" },
  { to: "/admin/states", labelKey: "admin.nav.states" },
  { to: "/admin/priorities", labelKey: "admin.nav.priorities" },
  { to: "/admin/customer-users", labelKey: "admin.nav.customerUsers" },
  { to: "/admin/customer-companies", labelKey: "admin.nav.customerCompanies" },
  { to: "/admin/templates", labelKey: "admin.nav.templates" },
  { to: "/admin/salutations", labelKey: "admin.nav.salutations" },
  { to: "/admin/signatures", labelKey: "admin.nav.signatures" },
  { to: "/admin/auto-responses", labelKey: "admin.nav.autoResponses" },
  { to: "/admin/postmaster-filters", labelKey: "admin.nav.postmasterFilters" },
  { to: "/admin/acl", labelKey: "admin.nav.acl" },
  { to: "/admin/generic-agent-jobs", labelKey: "admin.nav.genericAgentJobs" },
  { to: "/admin/dynamic-fields", labelKey: "admin.nav.dynamicFields" },
  { to: "/admin/processes", labelKey: "admin.nav.processes" },
];

export function AdminHomePage() {
  const { t } = useTranslation();
  return (
    <div className="space-y-4 p-4" data-testid="admin-home-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("admin.title")}</h1>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {LINKS.map((link) => (
          <Link
            key={link.to}
            to={link.to}
            className="rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-ink transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          >
            {t(link.labelKey)}
          </Link>
        ))}
      </div>
    </div>
  );
}
