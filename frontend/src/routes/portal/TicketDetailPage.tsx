import { useRef, useState, type FormEvent } from "react";
import { useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { portalApi, ApiError, type ArticleListItem } from "@/lib/portalApi";
import { stateColorVar } from "@/lib/status";
import { formatDateTime } from "@/lib/format";
import { Spinner } from "@/components/ui/Spinner";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

/**
 * Locally-known article bodies for this browser session.
 *
 * The portal articles API (`GET /api/portal/tickets/{id}/articles`) returns
 * metadata only — id, sender, subject, timestamps — never the article text.
 * There is currently no portal endpoint to fetch an individual article body,
 * so historical agent replies and older customer messages can only be shown
 * by their subject line here. We *do* know the text the customer themselves
 * just typed (ticket creation, reply, attachment note), so we cache those
 * locally and render them as full messages once the article list confirms
 * the corresponding article id.
 */
type KnownBodies = Record<number, string>;

export function TicketDetailPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const { ticketId: ticketIdParam } = useParams({ from: "/portal/tickets/$ticketId" });
  const ticketId = Number(ticketIdParam);
  const queryClient = useQueryClient();
  const [knownBodies, setKnownBodies] = useState<KnownBodies>({});
  const [replyBody, setReplyBody] = useState("");
  const [replyError, setReplyError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const ticketQ = useQuery({
    queryKey: ["portal", "tickets", ticketId],
    queryFn: () => portalApi.portalGetTicket(ticketId),
    enabled: Number.isFinite(ticketId),
  });

  const articlesQ = useQuery({
    queryKey: ["portal", "tickets", ticketId, "articles"],
    queryFn: () => portalApi.portalListArticles(ticketId),
    enabled: Number.isFinite(ticketId),
  });

  const invalidateTicket = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["portal", "tickets", ticketId] }),
      queryClient.invalidateQueries({ queryKey: ["portal", "tickets", ticketId, "articles"] }),
      queryClient.invalidateQueries({ queryKey: ["portal", "tickets"] }),
    ]);

  const replyMutation = useMutation({
    mutationFn: (text: string) => portalApi.portalReply(ticketId, { body: text }),
    onSuccess: async (res, text) => {
      setKnownBodies((m) => ({ ...m, [res.article_id]: text }));
      setReplyBody("");
      await invalidateTicket();
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => portalApi.portalUploadAttachment(ticketId, file),
    onSuccess: async () => {
      await invalidateTicket();
    },
  });

  if (!Number.isFinite(ticketId)) {
    return <p className="text-sm text-danger">{t("ticket.invalidId")}</p>;
  }

  if (ticketQ.isLoading) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  if (ticketQ.isError || !ticketQ.data) {
    return <p className="text-sm text-danger">{t("ticket.loadError")}</p>;
  }

  const ticket = ticketQ.data;
  const spineColor = stateColorVar(ticket.state);
  const articles = articlesQ.data ?? [];

  const onSubmitReply = async (e: FormEvent) => {
    e.preventDefault();
    setReplyError(null);
    const text = replyBody.trim();
    if (!text) return;
    try {
      await replyMutation.mutateAsync(text);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setReplyError(t("portal.ticket.followUpRejected"));
      } else {
        setReplyError(t("portal.ticket.replyFailed"));
      }
    }
  };

  const onPickFile = () => fileInputRef.current?.click();

  const onFileChosen = async () => {
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    try {
      await uploadMutation.mutateAsync(file);
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <div className="space-y-4" data-testid="portal-ticket-detail-page">
      <div
        className="status-spine rounded-lg border border-hairline bg-surface px-4 py-3 pl-5"
        style={{ "--spine-color": spineColor } as React.CSSProperties}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs tabular-nums text-muted">{ticket.tn}</span>
          <span
            className="rounded border px-1.5 py-0.5 text-[11px] font-medium capitalize"
            style={{ borderColor: spineColor, color: spineColor }}
          >
            {ticket.state || t("portal.tickets.unknownState")}
          </span>
        </div>
        <h1 className="mt-1 font-display text-lg font-semibold text-ink">
          {ticket.title || t("ticket.noTitle")}
        </h1>
      </div>

      <ArticleThread
        articles={articles}
        knownBodies={knownBodies}
        isLoading={articlesQ.isLoading}
        locale={locale}
      />

      <form
        onSubmit={(e) => void onSubmitReply(e)}
        className="space-y-2 rounded-lg border border-hairline bg-surface p-3"
        data-testid="portal-reply-form"
      >
        <label className="block text-sm">
          <span className="mb-1 block text-muted">{t("portal.ticket.replyLabel")}</span>
          <textarea
            data-testid="portal-reply-body"
            rows={4}
            value={replyBody}
            onChange={(e) => setReplyBody(e.target.value)}
            placeholder={t("portal.ticket.replyPlaceholder")}
            className="w-full resize-y rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
          />
        </label>
        {replyError && (
          <p className="text-sm text-danger" data-testid="portal-reply-error" role="alert">
            {replyError}
          </p>
        )}
        {uploadMutation.isError && (
          <p className="text-sm text-danger">{t("portal.ticket.uploadFailed")}</p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="submit"
            variant="primary"
            size="sm"
            disabled={replyMutation.isPending || !replyBody.trim()}
            data-testid="portal-reply-submit"
          >
            {replyMutation.isPending ? <Spinner /> : t("portal.ticket.send")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={onPickFile}
            disabled={uploadMutation.isPending}
            data-testid="portal-attach-btn"
          >
            {uploadMutation.isPending ? <Spinner /> : t("portal.ticket.attachFile")}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            data-testid="portal-attach-input"
            onChange={() => void onFileChosen()}
          />
        </div>
      </form>
    </div>
  );
}

function ArticleThread({
  articles,
  knownBodies,
  isLoading,
  locale,
}: {
  articles: ArticleListItem[];
  knownBodies: KnownBodies;
  isLoading: boolean;
  locale: string;
}) {
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  if (articles.length === 0) {
    return <p className="text-sm text-muted">{t("ticket.noArticles")}</p>;
  }

  return (
    <ul className="space-y-3" data-testid="portal-article-thread">
      {articles.map((article) => {
        const isCustomer = (article.sender_type ?? "").toLowerCase() === "customer";
        const known = knownBodies[article.id];
        return (
          <li
            key={article.id}
            className={cn("flex", isCustomer ? "justify-end" : "justify-start")}
            data-testid={`portal-article-${article.id}`}
          >
            <div
              className={cn(
                "max-w-[85%] rounded-lg border px-3 py-2",
                isCustomer
                  ? "border-accent/30 bg-accent/10"
                  : "border-hairline bg-surface",
              )}
            >
              <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
                <span className="font-medium text-ink">
                  {isCustomer ? t("portal.ticket.you") : t("portal.ticket.support")}
                </span>
                <span className="font-mono tabular-nums">
                  {formatDateTime(article.create_time, locale)}
                </span>
              </div>
              {known ? (
                <p className="mt-1 whitespace-pre-wrap break-words text-sm text-ink">
                  {known}
                </p>
              ) : (
                <>
                  <p className="mt-1 text-sm font-medium text-ink">
                    {article.subject || t("ticket.noSubject")}
                  </p>
                  <p className="mt-0.5 text-xs italic text-muted">
                    {t("portal.ticket.bodyUnavailable")}
                  </p>
                </>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
