import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { Spinner } from "@/components/ui/Spinner";

export function HistoryTable({ ticketId }: { ticketId: number }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const histQ = useQuery({
    queryKey: ["tickets", ticketId, "history"],
    queryFn: () => api.listHistory(ticketId),
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
    <div className="overflow-x-auto rounded-lg border border-hairline" data-testid="history-table">
      <table className="w-full min-w-[560px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
            <th className="px-2 py-1.5 font-medium">{t("ticket.historyTime")}</th>
            <th className="px-2 py-1.5 font-medium">{t("ticket.historyType")}</th>
            <th className="px-2 py-1.5 font-medium">{t("ticket.historyName")}</th>
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
              <td className="px-2 py-1 font-mono text-xs tabular-nums text-muted">
                {formatDateTime(h.create_time, locale)}
              </td>
              <td className="px-2 py-1 text-xs">{h.history_type || h.history_type_id}</td>
              <td className="px-2 py-1 font-mono text-xs">{h.name}</td>
              <td className="px-2 py-1 font-mono text-xs tabular-nums text-muted">{h.create_by}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
