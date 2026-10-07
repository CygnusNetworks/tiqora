import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError, api, type CustomerUserOut } from "@/lib/api";
import { phoneApi } from "@/lib/phoneApi";

export type CustomerDrawerState =
  | { mode: "create"; company?: { customer_id: string; name: string }; prefill?: Partial<FormValues> }
  | { mode: "edit"; customer: CustomerUserOut };

type FormValues = {
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  mobile: string;
  title: string;
  street: string;
  zip: string;
  city: string;
  comments: string;
  login: string;
};

const EMPTY: FormValues = {
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
  mobile: "",
  title: "",
  street: "",
  zip: "",
  city: "",
  comments: "",
  login: "",
};

const inputCls =
  "w-full rounded-md border border-hairline bg-bg px-2.5 py-1.5 text-sm text-ink focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent";

function useDebounced<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const h = window.setTimeout(() => setOut(value), ms);
    return () => window.clearTimeout(h);
  }, [value, ms]);
  return out;
}

/** The existing login named by a 409 e-mail conflict, if any. */
function conflictOwner(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.detail as { detail?: unknown } | null;
  const inner = body && typeof body === "object" ? body.detail : null;
  if (inner && typeof inner === "object" && "login" in inner) {
    const login = (inner as { login: unknown }).login;
    return typeof login === "string" && login !== "" ? login : null;
  }
  return null;
}

/**
 * Create or edit a customer user in a side panel next to the workbench
 * (agents with the customer_edit feature). Creating takes the e-mail as the
 * login unless the agent types another one; without a company the login is
 * also the customer number, as for private customers in Znuny.
 */
export function CustomerEditDrawer({
  state,
  onClose,
  onSaved,
}: {
  state: CustomerDrawerState;
  onClose: () => void;
  onSaved: (login: string) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const editing = state.mode === "edit" ? state.customer : null;

  const [values, setValues] = useState<FormValues>(() =>
    editing
      ? {
          first_name: editing.first_name ?? "",
          last_name: editing.last_name ?? "",
          email: editing.email ?? "",
          phone: editing.phone ?? "",
          mobile: editing.mobile ?? "",
          title: editing.title ?? "",
          street: editing.street ?? "",
          zip: editing.zip ?? "",
          city: editing.city ?? "",
          comments: editing.comments ?? "",
          login: editing.login,
        }
      : { ...EMPTY, ...(state.mode === "create" ? state.prefill : {}) },
  );
  const [loginTouched, setLoginTouched] = useState(false);
  const [company, setCompany] = useState<{ customer_id: string; name: string } | null>(() =>
    editing
      ? { customer_id: editing.customer_id, name: editing.company_name || editing.customer_id }
      : state.mode === "create"
        ? (state.company ?? null)
        : null,
  );

  const set = (key: keyof FormValues) => (e: { target: { value: string } }) =>
    setValues((prev) => ({ ...prev, [key]: e.target.value }));
  const login = editing ? editing.login : loginTouched ? values.login : values.email.trim();

  // Soft duplicate check: the number already belongs to someone else.
  const phoneTerm = useDebounced(values.phone.trim(), 400);
  const phoneOwnersQ = useQuery({
    queryKey: ["reference", "caller", phoneTerm],
    queryFn: ({ signal }) => phoneApi.callerLookup(phoneTerm, signal),
    enabled: phoneTerm.replace(/\D/g, "").length >= 5,
    staleTime: 60 * 1000,
  });
  const phoneOwners = (phoneOwnersQ.data?.customers ?? []).filter(
    (c) => c.login !== editing?.login,
  );

  const save = useMutation({
    mutationFn: async (): Promise<string> => {
      const optional = (v: string) => v.trim() || null;
      const customerId = company?.customer_id || login;
      if (editing) {
        const out = await api.updateCustomer(editing.login, {
          title: optional(values.title),
          first_name: values.first_name.trim(),
          last_name: values.last_name.trim(),
          email: values.email.trim(),
          customer_id: customerId,
          phone: optional(values.phone),
          mobile: optional(values.mobile),
          street: optional(values.street),
          zip: optional(values.zip),
          city: optional(values.city),
          comments: optional(values.comments),
        });
        return out.login;
      }
      const out = await api.createCustomer({
        login,
        email: values.email.trim(),
        first_name: values.first_name.trim(),
        last_name: values.last_name.trim(),
        customer_id: customerId,
        phone: optional(values.phone),
        mobile: optional(values.mobile),
        title: optional(values.title),
        street: optional(values.street),
        zip: optional(values.zip),
        city: optional(values.city),
        comments: optional(values.comments),
      });
      return out.login;
    },
    onSuccess: async (savedLogin) => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["customers", savedLogin] }),
        qc.invalidateQueries({ queryKey: ["customer-directory"] }),
      ]);
    },
  });

  const submit = async (then: "stay" | "email") => {
    try {
      const savedLogin = await save.mutateAsync();
      onSaved(savedLogin);
      if (then === "email") {
        void navigate({ to: "/agent/tickets/new", search: { type: "email", customer: savedLogin } });
      }
    } catch {
      // shown below from save.error
    }
  };

  const owner = conflictOwner(save.error);
  const errorText = save.error
    ? owner
      ? t("customerWorkbench.emailTaken", { login: owner })
      : save.error instanceof ApiError && save.error.status === 409
        ? t("ticket.dialog.customerLoginConflict")
        : t("ticket.dialog.genericError")
    : null;
  const canSave = values.last_name.trim() !== "" && login.trim() !== "" && !save.isPending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (canSave) void submit("stay");
  };

  return (
    <Dialog
      open
      onClose={onClose}
      placement="right"
      title={editing ? t("customerWorkbench.editTitle") : t("customerWorkbench.createTitle")}
      description={
        company && !editing
          ? t("customerWorkbench.createAt", { company: company.name })
          : undefined
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant={editing ? "primary" : "secondary"}
            disabled={!canSave}
            onClick={() => void submit("stay")}
            data-testid="customer-drawer-save"
          >
            {save.isPending && <Spinner className="h-3.5 w-3.5" />}
            {t("customerWorkbench.save")}
          </Button>
          {!editing && (
            <Button
              variant="primary"
              disabled={!canSave}
              onClick={() => void submit("email")}
              data-testid="customer-drawer-save-email"
            >
              {t("customerWorkbench.saveAndEmail")}
            </Button>
          )}
        </>
      }
      className="max-w-md"
    >
      <form className="space-y-3" onSubmit={onSubmit} data-testid="customer-drawer">
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("ticket.dialog.customerFirstName")}>
            <input className={inputCls} value={values.first_name} onChange={set("first_name")} />
          </Field>
          <Field label={t("ticket.dialog.customerLastName")}>
            <input
              className={inputCls}
              value={values.last_name}
              onChange={set("last_name")}
              required
              data-testid="customer-drawer-last-name"
            />
          </Field>
        </div>
        <Field label={t("ticket.dialog.customerEmail")}>
          <input
            className={inputCls}
            type="email"
            value={values.email}
            onChange={set("email")}
            data-testid="customer-drawer-email"
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("customerCentre.phone")}>
            <input
              className={inputCls}
              value={values.phone}
              onChange={set("phone")}
              data-testid="customer-drawer-phone"
            />
          </Field>
          <Field label={t("phone.mobileLabel")}>
            <input className={inputCls} value={values.mobile} onChange={set("mobile")} />
          </Field>
        </div>
        {phoneOwners.length > 0 && (
          <p
            className="rounded-md border border-escalation/30 bg-escalation/10 px-3 py-2 text-xs text-ink"
            data-testid="customer-drawer-phone-taken"
          >
            {t("customerWorkbench.phoneTaken", {
              names: phoneOwners.map((c) => c.name || c.login).join(", "),
            })}
          </p>
        )}
        <CompanyField company={company} onChange={setCompany} />
        <Field label={t("customerWorkbench.function")}>
          <input className={inputCls} value={values.title} onChange={set("title")} />
        </Field>
        <Field label={t("customerWorkbench.street")}>
          <input className={inputCls} value={values.street} onChange={set("street")} />
        </Field>
        <div className="grid grid-cols-[7rem_1fr] gap-3">
          <Field label={t("customerWorkbench.zip")}>
            <input className={inputCls} value={values.zip} onChange={set("zip")} />
          </Field>
          <Field label={t("customerWorkbench.city")}>
            <input className={inputCls} value={values.city} onChange={set("city")} />
          </Field>
        </div>
        <Field label={t("customerWorkbench.comments")}>
          <textarea
            className={`${inputCls} min-h-[4rem]`}
            value={values.comments}
            onChange={set("comments")}
            maxLength={250}
          />
        </Field>
        <Field label={t("customerWorkbench.login")}>
          <input
            className={`${inputCls} font-mono text-xs disabled:opacity-60`}
            value={login}
            disabled={Boolean(editing)}
            onChange={(e) => {
              setLoginTouched(true);
              setValues((prev) => ({ ...prev, login: e.target.value }));
            }}
            data-testid="customer-drawer-login"
          />
          <span className="mt-1 block text-xs text-muted">
            {editing ? t("customerWorkbench.loginFixed") : t("customerWorkbench.loginHint")}
          </span>
        </Field>
        {errorText && (
          <p className="text-xs text-danger" role="alert" data-testid="customer-drawer-error">
            {errorText}
          </p>
        )}
        {/* Enter in a field saves. */}
        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
      </form>
    </Dialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-muted">{label}</span>
      {children}
    </label>
  );
}

/** Company of the customer: search existing companies, or none (private). */
export function CompanyField({
  company,
  onChange,
}: {
  company: { customer_id: string; name: string } | null;
  onChange: (company: { customer_id: string; name: string } | null) => void;
}) {
  const { t } = useTranslation();
  const [picking, setPicking] = useState(false);
  const [term, setTerm] = useState("");
  const debounced = useDebounced(term.trim(), 250);
  const companiesQ = useQuery({
    queryKey: ["customer-directory", "companies", debounced],
    queryFn: ({ signal }) => api.searchCustomerDirectoryCompanies(debounced, signal),
    enabled: picking,
  });

  if (!picking) {
    return (
      <div>
        <span className="mb-1 block text-xs text-muted">{t("customerWorkbench.company")}</span>
        <div className="flex items-center gap-2 rounded-md border border-hairline bg-bg px-2.5 py-1.5">
          {company ? (
            <>
              <span className="truncate text-sm text-ink">{company.name}</span>
              <span className="font-mono text-[11px] text-muted">{company.customer_id}</span>
            </>
          ) : (
            <span className="text-sm text-muted">{t("customerWorkbench.noCompany")}</span>
          )}
          <button
            type="button"
            className="ml-auto text-xs font-medium text-accent hover:underline"
            onClick={() => setPicking(true)}
            data-testid="customer-drawer-company-change"
          >
            {t("customerWorkbench.change")}
          </button>
        </div>
      </div>
    );
  }
  const companies = companiesQ.data ?? [];
  return (
    <div>
      <span className="mb-1 block text-xs text-muted">{t("customerWorkbench.company")}</span>
      <input
        className={inputCls}
        autoFocus
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        placeholder={t("customerDirectory.companySearch")}
        data-testid="customer-drawer-company-search"
      />
      <ul className="mt-1 max-h-48 overflow-y-auto rounded-md border border-hairline">
        <li>
          <button
            type="button"
            className="w-full px-2.5 py-1.5 text-left text-sm text-muted hover:bg-surface-subtle"
            onClick={() => {
              onChange(null);
              setPicking(false);
            }}
          >
            {t("customerWorkbench.noCompany")}
          </button>
        </li>
        {companies.map((co) => (
          <li key={co.customer_id}>
            <button
              type="button"
              className="flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-sm hover:bg-surface-subtle"
              onClick={() => {
                onChange(co);
                setPicking(false);
              }}
              data-testid={`customer-drawer-company-${co.customer_id}`}
            >
              <span className="truncate text-ink">{co.name}</span>
              <span className="shrink-0 font-mono text-[11px] text-muted">{co.customer_id}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
