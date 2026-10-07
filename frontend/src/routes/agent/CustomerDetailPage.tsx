import { useState } from "react";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import {
  CustomerEditDrawer,
  type CustomerDrawerState,
} from "@/components/customers/CustomerEditDrawer";
import { CustomerPanel } from "@/components/customers/CustomerPanel";
import { ChevronLeftIcon } from "@/components/ui/icons";

/**
 * Customer Information Centre (agent): one customer on its own page — linked
 * from tickets and the dial page, open to every agent. Same panel as the
 * right side of the "Kunden" workbench.
 */
export function CustomerDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { login: loginParam } = useParams({ from: "/agent/customers/$login" });
  const login = decodeURIComponent(loginParam ?? "");
  const [drawer, setDrawer] = useState<CustomerDrawerState | null>(null);

  if (!login) {
    return <p className="p-6 text-sm text-danger">{t("customerCentre.invalid")}</p>;
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-5" data-testid="customer-detail-page">
      {user?.can_use_customer_directory && (
        <Link
          to="/agent/customers"
          search={{ sel: login }}
          className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink"
        >
          <ChevronLeftIcon className="text-[15px]" />
          {t("customerDirectory.title")}
        </Link>
      )}
      <CustomerPanel login={login} onEdit={(customer) => setDrawer({ mode: "edit", customer })} />
      {drawer && (
        <CustomerEditDrawer
          state={drawer}
          onClose={() => setDrawer(null)}
          onSaved={(saved) => {
            setDrawer(null);
            if (saved !== login) {
              void navigate({ to: "/agent/customers/$login", params: { login: saved } });
            }
          }}
        />
      )}
    </div>
  );
}
