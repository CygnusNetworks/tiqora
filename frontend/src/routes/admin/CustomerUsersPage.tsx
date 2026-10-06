import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import {
  api,
  ApiError,
  type AdminPage,
  type CustomerUserAdminOut,
  type CustomerUserAdminCreate,
  type CustomerUserAdminUpdate,
} from "@/lib/api";
import { AdminResourcePage, type AdminBulkAction } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { MenuItem } from "@/components/ui/Menu";
import { CustomerCryptoKeys } from "@/components/agent/CustomerCryptoKeys";
import { CustomerDirectoryAccessDialog } from "@/components/admin/CustomerDirectoryAccessDialog";
import { CustomerCompanyCell, CustomerNameCell } from "@/components/customers/CustomerCells";
import {
  downloadUrl,
  saveSelectedVcards,
  VCARD_EXPORT_MAX,
} from "@/components/customers/vcardExport";
import { DownloadIcon, KeyIcon, UsersIcon } from "@/components/ui/icons";
import { bulkInChunks } from "@/lib/bulk";
import { formatDateTime } from "@/lib/format";
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from "@/lib/passwordPolicy";

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

export function CustomerUsersPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [companyDialog, setCompanyDialog] = useState<{
    ids: Array<number | string>;
  } | null>(null);
  const [companySearch, setCompanySearch] = useState("");
  const [pickedCustomerId, setPickedCustomerId] = useState("");
  const [companyBusy, setCompanyBusy] = useState(false);
  const [companyError, setCompanyError] = useState<string | null>(null);
  // Znuny AdminCustomerUser shows the customer preference modules PGP/SMIME.
  const [keysLogin, setKeysLogin] = useState<string | null>(null);
  const [accessOpen, setAccessOpen] = useState(false);
  const debouncedCompanySearch = useDebouncedValue(companySearch, 300);

  const companiesQ = useQuery({
    queryKey: ["admin", "customer-companies-picker", debouncedCompanySearch],
    queryFn: ({ signal }) =>
      api.adminCustomerCompanies.list(
        {
          page: 1,
          pageSize: 25,
          valid: "valid",
          search: debouncedCompanySearch.trim() || undefined,
        },
        signal,
      ),
    enabled: companyDialog !== null,
  });

  // Name first; lower-priority columns drop out as the window narrows instead
  // of scrolling the table sideways (login/id stay in the edit dialog).
  const columns: DataTableColumn<CustomerUserAdminOut>[] = [
    {
      key: "name",
      header: t("admin.customerUsers.name"),
      sortable: true,
      sortKey: "last_name",
      render: (r) => (
        <CustomerNameCell
          firstName={r.first_name}
          lastName={r.last_name}
          email={r.email}
          login={r.login}
        />
      ),
    },
    {
      key: "customer_id",
      header: t("admin.customerUsers.company"),
      sortable: true,
      render: (r) => <CustomerCompanyCell companyName={r.company_name} customerId={r.customer_id} />,
    },
    {
      key: "phone",
      header: t("admin.customerUsers.phone"),
      hideBelow: "lg",
      render: (r) => <span className="tabular-nums text-muted">{r.phone || "–"}</span>,
    },
    {
      key: "login",
      header: t("admin.customerUsers.login"),
      sortable: true,
      mono: true,
      hideBelow: "2xl",
      render: (r) => r.login,
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      sortable: true,
      sortKey: "change_time",
      hideBelow: "xl",
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "login", label: t("admin.customerUsers.login"), type: "text", required: true },
    {
      name: "email",
      label: t("admin.customerUsers.email"),
      type: "text",
      required: true,
      help: {
        title: t("admin.customerUsers.email"),
        description: t("admin.help.customerUsers.email"),
      },
    },
    {
      name: "customer_id",
      label: t("admin.customerUsers.customerId"),
      type: "text",
      required: true,
      help: {
        title: t("admin.customerUsers.customerId"),
        description: t("admin.help.customerUsers.customerId"),
      },
    },
    {
      name: "password",
      label: t("admin.users.password"),
      type: "password",
      maxLength: MAX_PASSWORD_LENGTH,
      helpText: t("admin.users.passwordHelp", {
        min: MIN_PASSWORD_LENGTH,
        max: MAX_PASSWORD_LENGTH,
      }),
    },
    { name: "first_name", label: t("admin.customerUsers.firstName"), type: "text", required: true },
    { name: "last_name", label: t("admin.customerUsers.lastName"), type: "text", required: true },
    { name: "phone", label: t("admin.customerUsers.phone"), type: "text" },
    { name: "city", label: t("admin.customerUsers.city"), type: "text" },
    { name: "country", label: t("admin.customerUsers.country"), type: "text" },
    {
      name: "valid_id",
      label: t("admin.table.status"),
      type: "select",
      options: [
        { value: 1, label: t("admin.table.valid") },
        { value: 2, label: t("admin.table.invalid") },
      ],
      help: { title: t("admin.table.status"), description: t("admin.help.common.validId") },
    },
  ];

  // Resolve logins of selected ids from the currently cached list pages.
  const loginsFor = useCallback(
    (ids: Array<number | string>): string[] => {
      const loginById = new Map<number, string>();
      const cached = queryClient.getQueriesData<AdminPage<CustomerUserAdminOut>>({
        queryKey: ["admin", "customer-users"],
      });
      for (const [, data] of cached) {
        for (const row of data?.items ?? []) {
          loginById.set(row.id, row.login);
        }
      }
      return ids
        .map(Number)
        .map((id) => loginById.get(id))
        .filter((login): login is string => Boolean(login));
    },
    [queryClient],
  );

  const bulkActions: AdminBulkAction[] = useMemo(
    () => [
      {
        key: "valid",
        label: t("admin.customerUsers.bulk.valid"),
        run: (ids, { onProgress }) =>
          bulkInChunks(
            ids,
            (chunk) => api.bulkUpdateCustomerUsers({ ids: chunk.map(Number), valid_id: 1 }),
            onProgress,
          ),
      },
      {
        key: "invalid",
        label: t("admin.customerUsers.bulk.invalid"),
        run: (ids, { onProgress }) =>
          bulkInChunks(
            ids,
            (chunk) => api.bulkUpdateCustomerUsers({ ids: chunk.map(Number), valid_id: 2 }),
            onProgress,
          ),
      },
      {
        key: "temp",
        label: t("admin.customerUsers.bulk.temp"),
        run: (ids, { onProgress }) =>
          bulkInChunks(
            ids,
            (chunk) => api.bulkUpdateCustomerUsers({ ids: chunk.map(Number), valid_id: 3 }),
            onProgress,
          ),
      },
      {
        key: "company",
        label: t("admin.customerUsers.bulk.company"),
        run: async (ids) => {
          // Open the company picker dialog; mutation happens on apply.
          // Resolve immediately so the floating bar can clear selection while
          // the dialog retains the id list in local state.
          setCompanySearch("");
          setPickedCustomerId("");
          setCompanyError(null);
          setCompanyDialog({ ids });
        },
      },
      {
        key: "vcards",
        label: t("admin.customerUsers.bulk.vcards"),
        run: async (ids) => {
          const logins = loginsFor(ids);
          if (logins.length > VCARD_EXPORT_MAX) {
            throw new ApiError(
              422,
              t("customerDirectory.exportTooMany", { max: VCARD_EXPORT_MAX }),
              "",
            );
          }
          await saveSelectedVcards(logins);
        },
      },
      {
        key: "gdpr",
        label: t("admin.customerUsers.bulk.gdpr"),
        run: async (ids) => {
          await navigate({
            to: "/admin/gdpr",
            search: { logins: loginsFor(ids).join(",") },
          });
        },
      },
    ],
    [t, loginsFor, navigate],
  );

  const applyCompany = async () => {
    if (!companyDialog) return;
    const customerId = pickedCustomerId.trim();
    if (!customerId) {
      setCompanyError(t("admin.customerUsers.bulk.companyRequired"));
      return;
    }
    setCompanyBusy(true);
    setCompanyError(null);
    try {
      await bulkInChunks(companyDialog.ids, (chunk) =>
        api.bulkUpdateCustomerUsers({ ids: chunk.map(Number), customer_id: customerId }),
      );
      setCompanyDialog(null);
      await queryClient.invalidateQueries({ queryKey: ["admin", "customer-users"] });
    } catch (err) {
      setCompanyError(err instanceof Error ? err.message : t("admin.form.genericError"));
    } finally {
      setCompanyBusy(false);
    }
  };

  return (
    <>
      <AdminResourcePage
        resourceKey="customer-users"
        title={t("admin.customerUsers.title_plural")}
        newLabel={t("admin.customerUsers.new")}
        api={api.adminCustomerUsers}
        idOf={(r) => r.id}
        columns={columns}
        fields={fields}
        searchable
        searchRegexToggle
        sortable
        statusSortable
        pageSize={100}
        allowAllPageSize
        bulkActions={bulkActions}
        headerActions={
          <Button
            variant="secondary"
            size="sm"
            data-testid="admin-customer-directory-access"
            onClick={() => setAccessOpen(true)}
          >
            <UsersIcon className="text-[14px]" />
            {t("admin.customerUsers.access")}
          </Button>
        }
        rowActions={(row) => (
          <>
            <MenuItem
              testId={`admin-customer-user-vcard-${row.id}`}
              onSelect={() => downloadUrl(api.customerVcardUrl(row.login))}
            >
              <span className="inline-flex items-center gap-2">
                <DownloadIcon className="text-[14px]" />
                {t("customerCentre.downloadVcard")}
              </span>
            </MenuItem>
            <MenuItem
              testId={`admin-customer-user-keys-${row.id}`}
              onSelect={() => setKeysLogin(row.login)}
            >
              <span className="inline-flex items-center gap-2">
                <KeyIcon className="text-[14px]" />
                {t("cryptoKeys.openButton")}
              </span>
            </MenuItem>
          </>
        )}
        toFormValues={(row) =>
          row
            ? {
                login: row.login,
                email: row.email,
                customer_id: row.customer_id,
                password: "",
                first_name: row.first_name,
                last_name: row.last_name,
                phone: row.phone ?? "",
                city: row.city ?? "",
                country: row.country ?? "",
                valid_id: row.valid_id,
              }
            : { valid_id: 1 }
        }
        toCreateBody={(v: FieldValues): CustomerUserAdminCreate => ({
          login: v.login as string,
          email: v.email as string,
          customer_id: v.customer_id as string,
          password: (v.password as string) || null,
          first_name: v.first_name as string,
          last_name: v.last_name as string,
          phone: (v.phone as string) || null,
          city: (v.city as string) || null,
          country: (v.country as string) || null,
          valid_id: Number(v.valid_id) || 1,
        })}
        toUpdateBody={(v: FieldValues): CustomerUserAdminUpdate => ({
          login: v.login as string,
          email: v.email as string,
          customer_id: v.customer_id as string,
          first_name: v.first_name as string,
          last_name: v.last_name as string,
          phone: (v.phone as string) || null,
          city: (v.city as string) || null,
          country: (v.country as string) || null,
          valid_id: Number(v.valid_id) || 1,
          ...(v.password ? { password: v.password as string } : {}),
        })}
      />

      {accessOpen && <CustomerDirectoryAccessDialog onClose={() => setAccessOpen(false)} />}

      <Dialog
        open={keysLogin !== null}
        onClose={() => setKeysLogin(null)}
        title={t("cryptoKeys.dialogTitle", { login: keysLogin ?? "" })}
      >
        {keysLogin && <CustomerCryptoKeys login={keysLogin} framed={false} />}
      </Dialog>

      <Dialog
        open={companyDialog !== null}
        onClose={() => {
          if (!companyBusy) setCompanyDialog(null);
        }}
        title={t("admin.customerUsers.bulk.companyTitle")}
      >
        <div className="space-y-3" data-testid="admin-bulk-company-dialog">
          <p className="text-xs text-muted">
            {t("admin.customerUsers.bulk.companyHint", {
              count: companyDialog?.ids.length ?? 0,
            })}
          </p>
          <label className="block text-xs text-muted">
            {t("admin.customerUsers.customerId")}
            <input
              type="search"
              value={companySearch}
              onChange={(e) => {
                setCompanySearch(e.target.value);
                setPickedCustomerId(e.target.value);
              }}
              placeholder={t("admin.customerUsers.bulk.companySearch")}
              data-testid="admin-bulk-company-search"
              className="mt-1 w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </label>
          {companiesQ.data && companiesQ.data.items.length > 0 && (
            <ul
              className="max-h-40 overflow-y-auto rounded-md border border-hairline"
              data-testid="admin-bulk-company-results"
            >
              {companiesQ.data.items.map((co) => (
                <li key={co.customer_id}>
                  <button
                    type="button"
                    className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left text-xs hover:bg-surface-subtle"
                    data-testid={`admin-bulk-company-option-${co.customer_id}`}
                    onClick={() => {
                      setPickedCustomerId(co.customer_id);
                      setCompanySearch(co.customer_id);
                    }}
                  >
                    <span className="font-mono text-accent">{co.customer_id}</span>
                    <span className="truncate text-muted">{co.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {pickedCustomerId && (
            <p className="text-xs text-ink" data-testid="admin-bulk-company-picked">
              {t("admin.customerUsers.bulk.companyPicked", { id: pickedCustomerId })}
            </p>
          )}
          {companyError && (
            <p className="text-xs text-danger" data-testid="admin-bulk-company-error">
              {companyError}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={companyBusy}
              onClick={() => setCompanyDialog(null)}
            >
              {t("admin.form.cancel")}
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={companyBusy || !pickedCustomerId.trim()}
              data-testid="admin-bulk-company-apply"
              onClick={() => void applyCompany()}
            >
              {t("admin.customerUsers.bulk.companyApply")}
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  );
}
