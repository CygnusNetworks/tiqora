import { useState, type FormEvent } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { portalApi, ApiError } from "@/lib/portalApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { PORTAL_CI_PICKER_MAX, usePortalCis } from "@/components/cmdb/usePortalCmdb";

export type PortalNewTicketSearch = { ci?: number };

export function NewTicketPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const search = useSearch({ strict: false }) as PortalNewTicketSearch;
  const cis = usePortalCis();
  // `?ci=` preselects a device; a picked id the list no longer offers is dropped on submit.
  const [picked, setPicked] = useState<number[]>(() =>
    typeof search.ci === "number" && Number.isInteger(search.ci) ? [search.ci] : [],
  );

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!title.trim() || !body.trim()) {
      setError(t("portal.newTicket.validationError"));
      return;
    }
    setSubmitting(true);
    try {
      const offered = new Set(cis.items.map((c) => c.id));
      const ids = cis.available ? picked.filter((id) => offered.has(id)) : [];
      const res = await portalApi.portalCreateTicket({
        title,
        body,
        ...(ids.length > 0 ? { config_item_ids: ids } : {}),
      });
      await navigate({
        to: "/portal/tickets/$ticketId",
        params: { ticketId: String(res.ticket_id) },
      });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(t("portal.newTicket.submitError"));
      } else {
        throw err;
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-4" data-testid="portal-new-ticket-page">
      <h1 className="font-display text-xl font-semibold text-ink">
        {t("portal.newTicket.title")}
      </h1>
      <p className="text-sm text-muted">{t("portal.newTicket.intro")}</p>
      <form
        onSubmit={(e) => void onSubmit(e)}
        className="space-y-4 rounded-lg border border-hairline bg-surface p-4"
      >
        <label className="block text-sm">
          <span className="mb-1 block text-muted">{t("portal.newTicket.subject")}</span>
          <input
            data-testid="portal-new-ticket-subject"
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-muted">{t("portal.newTicket.message")}</span>
          <textarea
            data-testid="portal-new-ticket-body"
            required
            rows={6}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            className="w-full resize-y rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
          />
        </label>
        {cis.available && (
          <fieldset className="space-y-2 text-sm" data-testid="portal-new-ticket-cis">
            <legend className="mb-1 text-muted">{t("portal.cmdb.picker.label")}</legend>
            <p className="text-xs text-muted">
              {t("portal.cmdb.picker.hint", { max: PORTAL_CI_PICKER_MAX })}
            </p>
            <ul className="max-h-48 space-y-1 overflow-y-auto">
              {cis.items.map((ci) => {
                const checked = picked.includes(ci.id);
                const full = !checked && picked.length >= PORTAL_CI_PICKER_MAX;
                return (
                  <li key={ci.id}>
                    <label className="flex items-center gap-2 text-ink">
                      <input
                        type="checkbox"
                        data-testid={`portal-new-ticket-ci-${ci.id}`}
                        checked={checked}
                        disabled={full}
                        onChange={() =>
                          setPicked((cur) =>
                            checked ? cur.filter((x) => x !== ci.id) : [...cur, ci.id],
                          )
                        }
                      />
                      <span className="min-w-0 break-words">
                        {ci.name} <span className="text-xs text-muted">({ci.number})</span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
            {picked.length >= PORTAL_CI_PICKER_MAX && (
              <p className="text-xs text-muted">
                {t("portal.cmdb.picker.max", { max: PORTAL_CI_PICKER_MAX })}
              </p>
            )}
          </fieldset>
        )}
        {error && (
          <p className="text-sm text-danger" data-testid="portal-new-ticket-error" role="alert">
            {error}
          </p>
        )}
        <Button
          type="submit"
          variant="primary"
          disabled={submitting}
          data-testid="portal-new-ticket-submit"
        >
          {submitting ? <Spinner /> : t("portal.newTicket.submit")}
        </Button>
      </form>
    </div>
  );
}
