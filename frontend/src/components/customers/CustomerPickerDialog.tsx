import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { SearchIcon } from "@/components/ui/icons";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { usePatchTicket } from "@/lib/ticket";
import { CompanyField } from "./CustomerEditDrawer";
import { customerName, initialsOf, parseFrom } from "./customerFormat";

const inputCls =
  "w-full rounded-md border border-hairline bg-bg px-2.5 py-1.5 text-sm text-ink focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent";

type Candidate = {
  login: string;
  email: string;
  customer_id: string;
  name: string;
  /** Why it is suggested ("Absender", "Zuletzt"), shown under the name. */
  reason?: string;
};

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
 * Assign the ticket's customer. Search on the left (or suggestions: the
 * sender of the ticket and the agent's recent customers), a preview of the
 * highlighted customer on the right so the agent sees whom they assign.
 * "Neu anlegen" creates the customer prefilled from the ticket's sender and
 * assigns it in one go.
 */
export function CustomerPickerDialog({
  ticketId,
  currentCustomerId,
  currentCustomerUserId,
  senderFrom,
  onClose,
}: {
  ticketId: number;
  currentCustomerId?: string | null;
  currentCustomerUserId?: string | null;
  /** Raw From of the ticket's first article — suggestion and create prefill. */
  senderFrom?: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const sender = useMemo(() => parseFrom(senderFrom), [senderFrom]);
  const [mode, setMode] = useState<"search" | "create">("search");
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const term = useDebounced(q.trim(), 200);
  const patch = usePatchTicket(ticketId, onClose);
  const hasCurrent = Boolean(currentCustomerUserId || currentCustomerId);

  const searchQ = useQuery({
    queryKey: ["reference", "customers", term],
    queryFn: ({ signal }) => api.searchReferenceCustomers({ q: term }, signal),
    enabled: mode === "search" && term.length >= 2,
  });
  // Suggestions while nothing is typed: whoever owns the sender address …
  const senderQ = useQuery({
    queryKey: ["reference", "customers", sender.email],
    queryFn: ({ signal }) => api.searchReferenceCustomers({ q: sender.email }, signal),
    enabled: mode === "search" && sender.email !== "",
  });
  // … and the agent's own recent customers (customer-directory grant only).
  const shortlistQ = useQuery({
    queryKey: ["customer-directory", "shortlist"],
    queryFn: ({ signal }) => api.getCustomerShortlist(signal),
    enabled: mode === "search" && Boolean(user?.can_use_customer_directory),
    staleTime: 30 * 1000,
  });

  const candidates: Candidate[] = useMemo(() => {
    if (term.length >= 2) {
      return (searchQ.data ?? []).map((c) => ({ ...c, name: c.full_name || c.login }));
    }
    const out: Candidate[] = [];
    const seen = new Set<string>();
    const senderEmail = sender.email.toLowerCase();
    for (const c of senderQ.data ?? []) {
      if (c.email.toLowerCase() !== senderEmail || seen.has(c.login)) continue;
      seen.add(c.login);
      out.push({ ...c, name: c.full_name || c.login, reason: t("customerPicker.sender") });
    }
    for (const e of shortlistQ.data?.recent ?? []) {
      if (seen.has(e.login)) continue;
      seen.add(e.login);
      out.push({
        login: e.login,
        email: e.email,
        customer_id: e.customer_id,
        name: customerName(e),
        reason: t("customerPicker.recent"),
      });
    }
    return out;
  }, [term, searchQ.data, senderQ.data, shortlistQ.data, sender.email, t]);

  useEffect(() => setHi(0), [term]);
  const highlighted = candidates[Math.min(hi, candidates.length - 1)];
  const senderUnknown =
    sender.email !== "" &&
    senderQ.isSuccess &&
    !(senderQ.data ?? []).some((c) => c.email.toLowerCase() === sender.email.toLowerCase());

  const assign = (c: { login: string; customer_id: string }) =>
    patch.mutate({ customer_user_id: c.login, customer_id: c.customer_id });

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHi((i) => Math.min(i + 1, candidates.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && highlighted) {
      e.preventDefault();
      assign(highlighted);
    }
  };

  const loading = term.length >= 2 ? searchQ.isLoading : senderQ.isLoading && shortlistQ.isLoading;

  return (
    <Dialog open onClose={onClose} title={t("customerPicker.title")} size="2xl">
      <div className="space-y-3" data-testid="customer-picker-dialog">
        <div className="flex flex-wrap items-center gap-3">
          <div role="tablist" className="flex gap-0.5 rounded-lg border border-hairline bg-bg p-0.5">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "search"}
              onClick={() => setMode("search")}
              className={tabCls(mode === "search")}
            >
              {t("customerPicker.search")}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "create"}
              onClick={() => setMode("create")}
              className={tabCls(mode === "create")}
              data-testid="customer-picker-new"
            >
              {t("ticket.dialog.newCustomer")}
            </button>
          </div>
          <div
            className="ml-auto flex min-w-0 items-center gap-2 text-xs text-muted"
            data-testid="customer-picker-current"
          >
            <span>{t("customerPicker.current")}</span>
            <span className="truncate font-medium text-ink">
              {hasCurrent ? currentCustomerUserId || "—" : t("customerPicker.none")}
            </span>
            {currentCustomerId && currentCustomerId !== currentCustomerUserId && (
              <span className="font-mono text-[11px]">{currentCustomerId}</span>
            )}
            {hasCurrent && (
              <button
                type="button"
                className="font-medium text-danger hover:underline disabled:opacity-50"
                disabled={patch.isPending}
                onClick={() => patch.mutate({ clear_customer: true })}
                data-testid="customer-picker-clear"
              >
                {t("customerPicker.clear")}
              </button>
            )}
          </div>
        </div>

        {mode === "search" ? (
          <div className="flex flex-wrap gap-4">
            <div className="min-w-0 flex-[1_1_18rem]">
              <label className="relative block">
                <span className="sr-only">{t("customerWorkbench.searchLabel")}</span>
                <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[15px] text-muted" />
                <input
                  className={cn(inputCls, "py-2 pl-8")}
                  value={q}
                  autoFocus
                  placeholder={t("customerWorkbench.searchPeople")}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={onKeyDown}
                  data-testid="customer-picker-search"
                />
              </label>
              <div className="mt-2 max-h-72 overflow-y-auto" role="listbox">
                {term.length < 2 && candidates.length > 0 && (
                  <p className="px-2 pb-1 text-xs font-semibold text-muted">
                    {t("customerPicker.suggestions")}
                  </p>
                )}
                {loading ? (
                  <div className="flex justify-center py-4">
                    <Spinner className="h-4 w-4" />
                  </div>
                ) : candidates.length === 0 ? (
                  <p className="px-2 py-3 text-sm text-muted" data-testid="customer-picker-empty">
                    {term.length >= 2 ? t("customerWorkbench.noHits") : t("customerPicker.typeToSearch")}
                  </p>
                ) : (
                  candidates.map((c, i) => (
                    <button
                      key={c.login}
                      type="button"
                      role="option"
                      aria-selected={i === hi}
                      onMouseEnter={() => setHi(i)}
                      onClick={() => assign(c)}
                      disabled={patch.isPending}
                      data-testid={`customer-picker-result-${c.login}`}
                      className={cn(
                        "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm disabled:opacity-50",
                        i === hi ? "bg-accent-dim" : "hover:bg-surface-subtle",
                      )}
                    >
                      <Avatar email={c.email} initials={initialsOf(c.name)} size={28} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-ink">{c.name}</span>
                        <span className="block truncate text-xs text-muted">
                          {c.reason ? `${c.reason}, ${c.email}` : c.email}
                        </span>
                      </span>
                      {c.customer_id && (
                        <span
                          className="shrink-0 font-mono text-[11px] text-muted"
                          data-testid={`customer-picker-id-${c.login}`}
                          title={t("ticket.toolbar.customerNumber")}
                        >
                          {c.customer_id}
                        </span>
                      )}
                    </button>
                  ))
                )}
                {senderUnknown && term.length < 2 && (
                  <button
                    type="button"
                    onClick={() => setMode("create")}
                    className="mt-1 w-full rounded-md border border-dashed border-hairline px-2 py-2 text-left text-sm hover:bg-surface-subtle"
                    data-testid="customer-picker-create-sender"
                  >
                    <span className="block font-medium text-accent">
                      {t("customerPicker.createSender", { email: sender.email })}
                    </span>
                    <span className="block text-xs text-muted">{t("customerPicker.senderUnknown")}</span>
                  </button>
                )}
              </div>
            </div>
            <div className="min-w-0 flex-[1_1_16rem] rounded-lg border border-hairline bg-surface-subtle/40 p-4">
              {highlighted ? (
                <CandidatePreview
                  candidate={highlighted}
                  matchesSender={
                    sender.email !== "" && highlighted.email.toLowerCase() === sender.email.toLowerCase()
                  }
                  busy={patch.isPending}
                  onAssign={() => assign(highlighted)}
                />
              ) : (
                <p className="text-sm text-muted">{t("customerPicker.previewHint")}</p>
              )}
            </div>
          </div>
        ) : (
          <CreateAndAssign
            prefill={sender}
            busy={patch.isPending}
            onCreated={(c) => assign(c)}
            onCancel={() => setMode("search")}
          />
        )}
        {patch.isError && <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>}
      </div>
    </Dialog>
  );
}

const tabCls = (on: boolean) =>
  cn(
    "rounded-md px-3 py-1 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
    on ? "bg-surface-subtle text-ink" : "text-muted hover:text-ink",
  );

function CandidatePreview({
  candidate,
  matchesSender,
  busy,
  onAssign,
}: {
  candidate: Candidate;
  matchesSender: boolean;
  busy: boolean;
  onAssign: () => void;
}) {
  const { t } = useTranslation();
  const customerQ = useQuery({
    queryKey: ["customers", candidate.login],
    queryFn: ({ signal }) => api.getCustomer(candidate.login, signal),
  });
  const openQ = useQuery({
    queryKey: ["tickets", "customer-user-open", candidate.login],
    queryFn: ({ signal }) =>
      api.listTickets({ customer_user_id: candidate.login, state_type: "open", limit: 1 }, signal),
  });
  const c = customerQ.data;
  return (
    <div className="flex h-full flex-col gap-3 text-sm" data-testid="customer-picker-preview">
      <div className="flex items-center gap-3">
        <Avatar email={candidate.email} initials={initialsOf(candidate.name)} size={40} />
        <div className="min-w-0">
          <p className="truncate font-semibold text-ink">{candidate.name}</p>
          <p className="truncate text-xs text-muted">
            {c?.company_name || candidate.customer_id}
          </p>
        </div>
      </div>
      <dl className="space-y-1.5">
        <div>
          <dt className="text-xs text-muted">{t("customerCentre.email")}</dt>
          <dd className="break-all">{candidate.email || "—"}</dd>
        </div>
        {(c?.phone || c?.mobile) && (
          <div>
            <dt className="text-xs text-muted">{t("customerCentre.phone")}</dt>
            <dd className="tabular-nums">{c.phone || c.mobile}</dd>
          </div>
        )}
        <div>
          <dt className="text-xs text-muted">{t("customerWorkbench.tickets")}</dt>
          <dd>
            {openQ.isLoading
              ? "…"
              : t("customerWorkbench.openCount", { count: openQ.data?.total ?? 0 })}
          </dd>
        </div>
      </dl>
      {matchesSender && (
        <p className="rounded-md border border-accent/30 bg-accent-dim px-2.5 py-1.5 text-xs text-ink">
          {t("customerPicker.matchesSender")}
        </p>
      )}
      <Button
        variant="primary"
        className="mt-auto"
        disabled={busy}
        onClick={onAssign}
        data-testid="customer-picker-assign"
      >
        {t("customerPicker.assign", { name: candidate.name })}
      </Button>
    </div>
  );
}

/** Create a customer (prefilled from the sender) and assign it right away. */
function CreateAndAssign({
  prefill,
  busy,
  onCreated,
  onCancel,
}: {
  prefill: { email: string; first_name: string; last_name: string };
  busy: boolean;
  onCreated: (c: { login: string; customer_id: string }) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [firstName, setFirstName] = useState(prefill.first_name);
  const [lastName, setLastName] = useState(prefill.last_name);
  const [email, setEmail] = useState(prefill.email);
  const [phone, setPhone] = useState("");
  const [login, setLogin] = useState<string | null>(null);
  const [company, setCompany] = useState<{ customer_id: string; name: string } | null>(null);
  const effectiveLogin = (login ?? email).trim();

  const create = useMutation({
    mutationFn: () =>
      api.createCustomer({
        login: effectiveLogin,
        email: email.trim(),
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        customer_id: company?.customer_id || effectiveLogin,
        phone: phone.trim() || null,
      }),
    onSuccess: (c) => onCreated({ login: c.login, customer_id: c.customer_id }),
  });
  const isConflict = create.error instanceof ApiError && create.error.status === 409;
  const owner = isConflict ? conflictOwner(create.error) : null;
  const valid = lastName.trim() !== "" && effectiveLogin !== "";

  return (
    <div className="space-y-3" data-testid="customer-create-dialog">
      {prefill.email && (
        <p className="text-xs text-muted">{t("customerPicker.prefilled")}</p>
      )}
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs text-muted">
          {t("ticket.dialog.customerFirstName")}
          <input
            className={cn(inputCls, "mt-1")}
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            data-testid="customer-create-first-name"
          />
        </label>
        <label className="block text-xs text-muted">
          {t("ticket.dialog.customerLastName")}
          <input
            className={cn(inputCls, "mt-1")}
            value={lastName}
            autoFocus
            onChange={(e) => setLastName(e.target.value)}
            data-testid="customer-create-last-name"
          />
        </label>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-xs text-muted">
          {t("ticket.dialog.customerEmail")}
          <input
            className={cn(inputCls, "mt-1")}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            data-testid="customer-create-email"
          />
        </label>
        <label className="block text-xs text-muted">
          {t("customerCentre.phone")}
          <input
            className={cn(inputCls, "mt-1")}
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            data-testid="customer-create-phone"
          />
        </label>
      </div>
      <CompanyField company={company} onChange={setCompany} />
      <label className="block text-xs text-muted">
        {t("ticket.dialog.customerLogin")}
        <input
          className={cn(inputCls, "mt-1 font-mono text-xs")}
          value={effectiveLogin}
          onChange={(e) => setLogin(e.target.value)}
          data-testid="customer-create-login"
        />
        <span className="mt-1 block">{t("customerWorkbench.loginHint")}</span>
      </label>
      {isConflict && (
        <p className="text-xs text-danger" data-testid="customer-create-conflict">
          {owner
            ? t("ticket.dialog.customerEmailConflict", { login: owner })
            : t("ticket.dialog.customerLoginConflict")}
        </p>
      )}
      {create.isError && !isConflict && (
        <p className="text-xs text-danger">{t("ticket.dialog.genericError")}</p>
      )}
      <div className="flex justify-end gap-2 pt-1">
        <Button variant="ghost" onClick={onCancel}>
          {t("ticket.dialog.back")}
        </Button>
        <Button
          variant="primary"
          disabled={!valid || create.isPending || busy}
          onClick={() => create.mutate()}
          data-testid="customer-create-submit"
        >
          {create.isPending && <Spinner className="h-3.5 w-3.5" />}
          {t("customerPicker.createAndAssign")}
        </Button>
      </div>
    </div>
  );
}
