import { useState, type FormEvent } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { portalApi, ApiError } from "@/lib/portalApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

export function NewTicketPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!title.trim() || !body.trim()) {
      setError(t("portal.newTicket.validationError"));
      return;
    }
    setSubmitting(true);
    try {
      const res = await portalApi.portalCreateTicket({ title, body });
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
