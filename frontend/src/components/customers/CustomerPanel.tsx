import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { useCustomerCryptoKeys } from "@/components/agent/customerCryptoKeysQuery";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { StateChip } from "@/components/ui/StatusChip";
import { Spinner } from "@/components/ui/Spinner";
import { BuildingIcon, DownloadIcon, PencilIcon, StarIcon } from "@/components/ui/icons";
import { api, type CustomerUserOut } from "@/lib/api";
import { avatarApi } from "@/lib/avatarApi";
import { cn } from "@/lib/cn";
import { humanDuration } from "@/lib/status";
import { isAvatarImage, isVcardFile, useFileDrop } from "@/lib/useFileDrop";
import { DropOverlay, EditableAvatar } from "./AvatarEditor";
import { useAvatarEditor } from "./useAvatarEditor";
import { CallAction, EmailTicketLink, PhoneTicketLink } from "./CustomerActions";
import { CustomerAiPause } from "./CustomerAiPause";
import { dropRejectMessage } from "./dropMessage";
import {
  CustomerKeysAddLink,
  CustomerKeysDialog,
  CustomerKeysSummary,
  EncryptedReachChip,
} from "./CustomerKeys";
import { customerName, initialsOf } from "./customerFormat";
import { useCustomerShortlist, useFavoriteToggle } from "./favorites";
import { vcardDragProps, vcfFileName } from "./vcardExport";
import { VcardApplyDialog } from "./VcardImport";

const TICKETS_SHOWN = 15;

type TicketFilter = "open" | "all";

/** One row of the inspector's contact list. */
function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3">
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

/** The six-dot handle that says "this can be dragged". */
function GripIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0 text-muted" fill="currentColor" aria-hidden="true">
      {[6, 12, 18].map((y) => (
        <g key={y}>
          <circle cx="9" cy={y} r="1.6" />
          <circle cx="15" cy={y} r="1.6" />
        </g>
      ))}
    </svg>
  );
}

/**
 * One customer user: their tickets take the room, the details sit in an
 * inspector on the right — contact data (numbers dial), the AI automation
 * switch and the vCard, which can be dragged out to Finder or Contacts.
 * Agents who may edit customers can set the picture from the avatar menu or
 * by dropping an image — or a contact from Apple Contacts — onto the panel.
 */
export function CustomerPanel({
  login,
  onEdit,
  onOpenCompany,
}: {
  login: string;
  /** Shown only to agents who may edit customers. */
  onEdit?: (customer: CustomerUserOut) => void;
  /** Company name click; default links to the company view of the "Kunden" page. */
  onOpenCompany?: (customerId: string) => void;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [keysOpen, setKeysOpen] = useState(false);
  // null = not picked yet: open tickets, or all when none is open.
  const [picked, setPicked] = useState<TicketFilter | null>(null);

  const customerQ = useQuery({
    queryKey: ["customers", login],
    queryFn: ({ signal }) => api.getCustomer(login, signal),
    enabled: Boolean(login),
  });
  const openQ = useQuery({
    queryKey: ["tickets", "customer-user-open", login, TICKETS_SHOWN],
    queryFn: ({ signal }) =>
      api.listTickets(
        {
          customer_user_id: login,
          state_type: "open",
          limit: TICKETS_SHOWN,
          sort: "changed",
          order: "desc",
        },
        signal,
      ),
    enabled: Boolean(login),
  });
  const allQ = useQuery({
    queryKey: ["tickets", "customer-user", login, TICKETS_SHOWN],
    queryFn: ({ signal }) =>
      api.listTickets(
        { customer_user_id: login, limit: TICKETS_SHOWN, sort: "changed", order: "desc" },
        signal,
      ),
    enabled: Boolean(login),
  });
  const keysQ = useCustomerCryptoKeys(login);
  // Favorites live on the "Kunden" page, so starring needs its grant.
  const canStar = Boolean(user?.can_use_customer_directory);
  const shortlistQ = useCustomerShortlist(canStar);
  const toggleFavorite = useFavoriteToggle();

  const canEdit = Boolean(user?.can_edit_customers);
  const avatar = useAvatarEditor({
    upload: (file) => avatarApi.uploadCustomerAvatar(login, file),
    remove: () => avatarApi.deleteCustomerAvatar(login),
  });
  const [vcardFile, setVcardFile] = useState<File | null>(null);
  const drop = useFileDrop({
    accept: (f) => isAvatarImage(f) || isVcardFile(f),
    onFiles: ([file]) => {
      if (isVcardFile(file)) setVcardFile(file);
      else void avatar.uploadFile(file);
    },
    onReject: (r) => avatar.setError(dropRejectMessage(t, r, "avatars.wrongDrop")),
    disabled: !canEdit || !customerQ.data,
    vcardText: true,
  });

  if (customerQ.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }
  if (customerQ.isError || !customerQ.data) {
    return (
      <p className="p-6 text-sm text-danger" data-testid="customer-panel-error">
        {t("customerCentre.loadError")}
      </p>
    );
  }

  const c = customerQ.data;
  const name = customerName(c);
  const phone = c.phone?.trim();
  const mobile = c.mobile?.trim();
  const address = [c.street, [c.zip, c.city].filter(Boolean).join(" "), c.country]
    .map((part) => (part ?? "").trim())
    .filter(Boolean)
    .join(", ");
  const openCount = openQ.data?.total ?? 0;
  const total = allQ.data?.total ?? 0;
  const filter: TicketFilter = picked ?? (openQ.isSuccess && openCount === 0 ? "all" : "open");
  const listQ = filter === "open" ? openQ : allQ;
  const tickets = listQ.data?.items ?? [];
  const shownTotal = listQ.data?.total ?? 0;
  const invalid = c.valid_id != null && c.valid_id !== 1;
  const favorite = Boolean(shortlistQ.data?.favorites.some((f) => f.login === c.login));
  const vcardUrl = api.customerVcardUrl(c.login);

  const companyLabel = c.company_name || c.customer_id;
  const companyLink = onOpenCompany ? (
    <button
      type="button"
      onClick={() => onOpenCompany(c.customer_id)}
      className="inline-flex items-center gap-1.5 font-medium text-ink hover:underline"
      data-testid="customer-panel-company"
    >
      <BuildingIcon className="text-[15px] text-muted" />
      {companyLabel}
    </button>
  ) : !user?.can_use_customer_directory ? (
    <span className="inline-flex items-center gap-1.5 font-medium text-ink" data-testid="customer-panel-company">
      <BuildingIcon className="text-[15px] text-muted" />
      {companyLabel}
    </span>
  ) : (
    <Link
      to="/agent/customers"
      search={{ tab: "companies", company: c.customer_id }}
      className="inline-flex items-center gap-1.5 font-medium text-ink hover:underline"
      data-testid="customer-panel-company"
    >
      <BuildingIcon className="text-[15px] text-muted" />
      {companyLabel}
    </Link>
  );

  return (
    <div className="relative flex flex-wrap items-start gap-6" data-testid="customer-panel" {...drop.dropProps}>
      <div className="min-w-0 flex-[999_1_420px] space-y-4">
        <header className="flex flex-wrap items-center gap-3">
          <EditableAvatar editor={avatar} canEdit={canEdit} kind="photo" testId="customer-avatar">
            <Avatar
              email={c.email}
              version={avatar.version}
              initials={initialsOf(name)}
              size={44}
              testId="customer-avatar-image"
            />
          </EditableAvatar>
          <div className="min-w-0 flex-1">
            <h1 className="font-display text-xl font-semibold text-ink" data-testid="customer-name">
              {name}
              {invalid && (
                <span className="ml-2 align-middle text-xs font-normal text-danger">
                  {t("customerWorkbench.invalid")}
                </span>
              )}
            </h1>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
              {c.title && <span>{c.title}</span>}
              {companyLink}
              <span className="font-mono text-xs">{c.customer_id}</span>
              <EncryptedReachChip data={keysQ.data} />
            </div>
          </div>
          {canStar && (
            <Button
              variant="ghost"
              onClick={() => toggleFavorite.mutate({ customer: c, favorite: !favorite })}
              aria-pressed={favorite}
              disabled={!shortlistQ.data}
              title={favorite ? t("customerWorkbench.favoriteRemove") : t("customerWorkbench.favoriteAdd")}
              data-testid="customer-favorite"
            >
              <StarIcon
                className={cn("text-[15px]", favorite && "text-warn")}
                fill={favorite ? "currentColor" : "none"}
              />
              {t("customerWorkbench.favorite")}
            </Button>
          )}
          {onEdit && user?.can_edit_customers && (
            <Button variant="ghost" onClick={() => onEdit(c)} data-testid="customer-edit">
              <PencilIcon className="text-[14px]" />
              {t("customerWorkbench.edit")}
            </Button>
          )}
        </header>
        {avatar.error && (
          <p className="-mt-2 text-sm text-danger" role="alert" data-testid="customer-avatar-error">
            {avatar.error}
          </p>
        )}

        <div className="flex flex-wrap items-start gap-2" data-testid="customer-actions">
          <EmailTicketLink login={c.login} primary />
          <PhoneTicketLink login={c.login} />
        </div>

        <section className="space-y-3" aria-labelledby="customer-tickets-title">
          <h2 id="customer-tickets-title" className="sr-only">
            {t("customerWorkbench.tickets")}
          </h2>
          <div className="flex flex-wrap items-center gap-3">
            <SegmentedControl<TicketFilter>
              items={[
                { value: "open", label: t("customerWorkbench.filterOpen", { n: openCount }) },
                { value: "all", label: t("customerWorkbench.filterAll", { n: total }) },
              ]}
              value={filter}
              onChange={setPicked}
              aria-label={t("customerWorkbench.ticketFilter")}
              testId="customer-ticket-filter"
            />
            {shownTotal > TICKETS_SHOWN && (
              <Link
                to="/agent/search"
                search={{ customer_id: c.customer_id, customer_label: companyLabel }}
                className="ml-auto text-xs text-accent hover:underline"
              >
                {t("customerWorkbench.companyTickets")}
              </Link>
            )}
          </div>
          <div className="rounded-lg border border-hairline bg-surface">
            {listQ.isLoading ? (
              <div className="flex justify-center py-4">
                <Spinner className="h-4 w-4" />
              </div>
            ) : tickets.length === 0 ? (
              <p className="px-3.5 py-3 text-sm text-muted" data-testid="customer-no-tickets">
                {filter === "open" ? t("customerWorkbench.noOpenTickets") : t("customerWorkbench.noTickets")}
              </p>
            ) : (
              <ul className="divide-y divide-hairline">
                {tickets.map((tk) => (
                  <li key={tk.id}>
                    <Link
                      to="/agent/tickets/$ticketId"
                      params={{ ticketId: String(tk.id) }}
                      className="flex items-center gap-3 px-3.5 py-2.5 hover:bg-surface-subtle"
                      data-testid={`customer-ticket-${tk.id}`}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-ink">{tk.title}</span>
                        <span className="block font-mono text-[11px] text-muted">{tk.tn}</span>
                      </span>
                      <StateChip state={tk.state} />
                      {tk.age_seconds != null && (
                        <span className="w-20 shrink-0 text-right text-xs text-muted">
                          {humanDuration(t, tk.age_seconds)}
                        </span>
                      )}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>

      <aside
        className="w-full min-w-0 flex-[1_1_280px] divide-y divide-hairline rounded-lg border border-hairline bg-surface text-sm lg:max-w-[340px]"
        aria-label={t("customerWorkbench.details")}
        data-testid="customer-inspector"
      >
        <section className="space-y-2.5 px-4 py-3.5" aria-labelledby="customer-contact-title">
          <h2 id="customer-contact-title" className="text-sm font-semibold text-ink">
            {t("customerWorkbench.contactSection")}
          </h2>
          <dl className="space-y-2">
            <DetailRow label={t("customerCentre.email")}>
              {c.email ? (
                <a href={`mailto:${c.email}`} className="break-all text-ink hover:underline">
                  {c.email}
                </a>
              ) : (
                "—"
              )}
            </DetailRow>
            {phone && (
              <DetailRow label={t("customerWorkbench.phone")}>
                <CallAction customer={c} number={phone} inline />
              </DetailRow>
            )}
            {mobile && (
              <DetailRow label={t("customerWorkbench.mobile")}>
                <CallAction customer={c} number={mobile} kind="mobile" inline />
              </DetailRow>
            )}
            {address && <DetailRow label={t("customerWorkbench.address")}>{address}</DetailRow>}
            <DetailRow label={t("customerWorkbench.login")}>
              <span className="break-all font-mono text-xs">{c.login}</span>
            </DetailRow>
            {c.comments && (
              <DetailRow label={t("customerWorkbench.comments")}>
                <span className="whitespace-pre-line">{c.comments}</span>
              </DetailRow>
            )}
          </dl>
          <dl className="empty:hidden">
            <CustomerKeysSummary data={keysQ.data} onOpen={() => setKeysOpen(true)} />
          </dl>
        </section>

        <div className="px-4 py-3.5">
          <CustomerAiPause scope="user" customerKey={c.login} canEdit={canEdit} framed={false} />
        </div>

        <section className="space-y-2.5 px-4 py-3.5" aria-labelledby="customer-vcard-title">
          <h2 id="customer-vcard-title" className="text-sm font-semibold text-ink">
            {t("customerWorkbench.vcard")}
          </h2>
          <a
            href={vcardUrl}
            download
            {...vcardDragProps(vcardUrl, name)}
            title={t("customerWorkbench.vcardDrag")}
            className="flex cursor-grab items-center gap-3 rounded-lg border border-hairline bg-surface-subtle/50 px-3 py-2.5 transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent active:cursor-grabbing"
            data-testid="customer-vcard"
          >
            <span className="grid h-10 w-8 shrink-0 place-items-center rounded border border-hairline bg-surface text-[10px] font-semibold text-accent">
              VCF
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium text-ink">{vcfFileName(name)}</span>
              <span className="block text-xs text-muted">{t("customerWorkbench.vcardTileHint")}</span>
            </span>
            <GripIcon />
          </a>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {/* A company export is a list export: needs the customer-directory grant. */}
            {c.company_name && user?.can_use_customer_directory && (
              <a
                href={api.companyVcardsUrl(c.customer_id)}
                download
                {...vcardDragProps(api.companyVcardsUrl(c.customer_id), companyLabel)}
                title={t("customerWorkbench.vcardDrag")}
                className="inline-flex items-center gap-1 text-accent hover:underline"
                data-testid="company-vcards"
              >
                <DownloadIcon className="text-[13px]" />
                {t("customerWorkbench.companyVcards")}
              </a>
            )}
            <CustomerKeysAddLink data={keysQ.data} onOpen={() => setKeysOpen(true)} />
          </div>
        </section>
      </aside>

      <CustomerKeysDialog login={c.login} open={keysOpen} onClose={() => setKeysOpen(false)} />

      {vcardFile && (
        <VcardApplyDialog
          file={vcardFile}
          customer={c}
          onClose={() => setVcardFile(null)}
          onApplied={(photo) => {
            setVcardFile(null);
            if (photo) avatar.bump();
          }}
        />
      )}
      <DropOverlay active={drop.active}>{t("avatars.dropCustomer")}</DropOverlay>
    </div>
  );
}
