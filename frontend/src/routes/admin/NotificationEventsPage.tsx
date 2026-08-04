import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type NotificationEventOut } from "@/lib/api";
import { Button } from "@/components/ui/Button";

const inputClass =
  "rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent";

export function NotificationEventsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const listQ = useQuery({
    queryKey: ["admin", "notification-events"],
    queryFn: () => api.listNotificationEvents(),
  });
  const [name, setName] = useState("");
  const [event, setEvent] = useState("TicketCreate");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");

  const createM = useMutation({
    mutationFn: () =>
      api.createNotificationEvent({
        name,
        valid_id: 1,
        items: { Events: [event] },
        messages: [
          {
            language: "en",
            subject: subject || name,
            text: body || name,
            content_type: "text/plain",
          },
        ],
      }),
    onSuccess: () => {
      setName("");
      setSubject("");
      setBody("");
      void qc.invalidateQueries({ queryKey: ["admin", "notification-events"] });
    },
  });

  const deactivateM = useMutation({
    mutationFn: (id: number) => api.deleteNotificationEvent(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin", "notification-events"] }),
  });

  const rows: NotificationEventOut[] = listQ.data ?? [];

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6" data-testid="notification-events-page">
      <div>
        <h1 className="text-xl font-semibold text-ink">
          {t("admin.notificationEvents.title_plural")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.notificationEvents.subtitle")}</p>
      </div>

      <form
        className="space-y-3 rounded-lg border border-line bg-surface p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) createM.mutate();
        }}
      >
        <div className="flex flex-wrap gap-3">
          <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-sm">
            <span>{t("admin.notificationEvents.name")}</span>
            <input
              className={inputClass}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </label>
          <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-sm">
            <span>{t("admin.notificationEvents.event")}</span>
            <input
              className={inputClass}
              value={event}
              onChange={(e) => setEvent(e.target.value)}
              required
            />
          </label>
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span>{t("admin.notificationEvents.subject")}</span>
          <input
            className={inputClass}
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span>{t("admin.notificationEvents.body")}</span>
          <textarea
            className="min-h-[5rem] rounded-md border border-line bg-surface px-3 py-2 text-sm"
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
        <Button type="submit" disabled={createM.isPending}>
          {t("admin.notificationEvents.new")}
        </Button>
      </form>

      <ul className="divide-y divide-line rounded-lg border border-line">
        {rows.map((r) => (
          <li key={r.id} className="flex items-center justify-between gap-3 px-4 py-3">
            <div>
              <div className="font-medium text-ink">{r.name}</div>
              <div className="text-xs text-muted">
                {(r.items?.Events ?? []).join(", ") || "—"} · valid={r.valid_id}
              </div>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => deactivateM.mutate(r.id)}
            >
              {t("admin.table.deactivate")}
            </Button>
          </li>
        ))}
        {rows.length === 0 && !listQ.isLoading && (
          <li className="px-4 py-6 text-center text-sm text-muted">{t("admin.table.empty")}</li>
        )}
      </ul>
    </div>
  );
}
