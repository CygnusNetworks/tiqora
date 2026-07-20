import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

export function HistoryTable({ ticketId }: { ticketId: number }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  // Default DESCENDING (newest first), matching the backend default.
  const [order, setOrder] = useState<"asc" | "desc">("desc");

  const histQ = useQuery({
    queryKey: ["tickets", ticketId, "history", order],
    queryFn: () => api.listHistory(ticketId, order),
  });

  if (histQ.isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  const rows = histQ.data ?? [];

  return (
    <div className="space-y-2" data-testid="history-panel">
      <div className="flex justify-end">
        <Button
          variant="secondary"
          size="sm"
          data-testid="history-sort-toggle"
          onClick={() => setOrder((o) => (o === "desc" ? "asc" : "desc"))}
        >
          {order === "desc" ? t("ticket.historySortDesc") : t("ticket.historySortAsc")}
        </Button>
      </div>
      <div
        className="overflow-x-auto rounded-lg border border-hairline"
        data-testid="history-table"
      >
        <table className="w-full min-w-[560px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
              <th className="px-2 py-1.5 font-medium">{t("ticket.historyTime")}</th>
              <th className="px-2 py-1.5 font-medium">{t("ticket.historyType")}</th>
              <th className="px-2 py-1.5 font-medium">{t("ticket.historyRendered")}</th>
              <th className="px-2 py-1.5 font-medium">{t("ticket.historyBy")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-6 text-center text-muted">
                  {t("ticket.noHistory")}
                </td>
              </tr>
            )}
            {rows.map((h) => (
              <tr key={h.id} className="border-b border-hairline/60 hover:bg-surface-subtle">
                <td className="whitespace-nowrap px-2 py-1 font-mono text-xs tabular-nums text-muted">
                  {formatDateTime(h.create_time, locale)}
                </td>
                <td className="px-2 py-1 text-xs text-muted">
                  {h.history_type || h.history_type_id}
                </td>
                <td
                  className="px-2 py-1 text-sm text-ink"
                  data-testid={`history-rendered-${h.id}`}
                >
                  {h.rendered}
                </td>
                <td className="whitespace-nowrap px-2 py-1 text-xs text-muted">
                  {h.create_by_login ?? h.create_by}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
