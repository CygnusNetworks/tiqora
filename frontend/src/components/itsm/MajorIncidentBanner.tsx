import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { itsmApi, itsmKeys } from "@/lib/itsmApi";

/** Banner on a ticket linked to an active major incident (one the caller may read). */
export function MajorIncidentBanner({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const itsmQ = useQuery({
    queryKey: itsmKeys.ticketItsm(ticketId),
    queryFn: ({ signal }) => itsmApi.ticketItsm(ticketId, signal),
    retry: false,
  });
  const banner = itsmQ.data?.enabled ? itsmQ.data.major_banner : null;
  if (!banner) return null;
  return (
    <div
      className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-ink"
      role="alert"
      data-testid="major-banner"
    >
      <span className="font-medium">{t("itsm.major.banner")}</span>{" "}
      <a
        href={`/agent/tickets/${banner.ticket_id}`}
        className="font-mono text-accent hover:underline"
        data-testid="major-banner-link"
      >
        {banner.tn}
      </a>{" "}
      <span>{banner.title}</span>
      {banner.summary && (
        <p className="mt-1 text-xs text-muted" data-testid="major-banner-summary">
          {banner.summary}
        </p>
      )}
    </div>
  );
}
