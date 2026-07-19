import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type ArticleListItem } from "@/lib/api";
import { formatDateTime, formatBytes } from "@/lib/format";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { ArticleBodyRenderer } from "./ArticleBodyRenderer";
import { cn } from "@/lib/cn";

function senderTone(
  senderType: string | null | undefined,
): "accent" | "muted" | "warn" | "default" {
  const s = (senderType || "").toLowerCase();
  if (s === "customer") return "accent";
  if (s === "agent") return "default";
  if (s === "system") return "muted";
  return "warn";
}

function dayKey(iso: string, locale: string): string {
  const d = new Date(iso);
  return new Intl.DateTimeFormat(locale, { dateStyle: "full" }).format(d);
}

export function ArticleTimeline({ ticketId }: { ticketId: number }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  const articlesQ = useQuery({
    queryKey: ["tickets", ticketId, "articles"],
    queryFn: () => api.listArticles(ticketId),
  });

  if (articlesQ.isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  const articles = articlesQ.data ?? [];
  if (articles.length === 0) {
    return <p className="text-sm text-muted">{t("ticket.noArticles")}</p>;
  }

  // Group by day
  const groups: { day: string; items: ArticleListItem[] }[] = [];
  for (const a of articles) {
    const day = dayKey(a.create_time, locale);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.items.push(a);
    else groups.push({ day, items: [a] });
  }

  return (
    <div className="space-y-6" data-testid="article-timeline">
      {groups.map((g) => (
        <section key={g.day}>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            {g.day}
          </h3>
          <ul className="space-y-3">
            {g.items.map((article) => {
              const open = expanded[article.id] ?? false;
              return (
                <li
                  key={article.id}
                  className="rounded-lg border border-border bg-surface-elevated"
                  data-testid={`article-${article.id}`}
                >
                  <button
                    type="button"
                    className="flex w-full flex-wrap items-start gap-2 px-3 py-2 text-left"
                    onClick={() =>
                      setExpanded((m) => ({ ...m, [article.id]: !open }))
                    }
                  >
                    <span
                      className={cn(
                        "mt-0.5 text-xs text-muted transition",
                        open && "rotate-90",
                      )}
                    >
                      ▶
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge tone={senderTone(article.sender_type)}>
                          {article.sender_type || t("ticket.unknownSender")}
                        </Badge>
                        <Badge
                          tone={
                            article.is_visible_for_customer ? "success" : "muted"
                          }
                        >
                          {article.is_visible_for_customer
                            ? t("ticket.visibleCustomer")
                            : t("ticket.internal")}
                        </Badge>
                        <span className="text-xs text-muted">
                          {formatDateTime(article.create_time, locale)}
                        </span>
                      </div>
                      <p className="mt-0.5 truncate text-sm font-medium text-ink">
                        {article.subject || t("ticket.noSubject")}
                      </p>
                      <p className="truncate text-xs text-muted">
                        {article.from_address}
                        {article.to_address ? ` → ${article.to_address}` : ""}
                      </p>
                    </div>
                  </button>
                  {open && (
                    <div className="space-y-3 border-t border-border px-3 py-3">
                      <ArticleBodyLoader
                        ticketId={ticketId}
                        articleId={article.id}
                      />
                      <AttachmentList
                        ticketId={ticketId}
                        articleId={article.id}
                      />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

function ArticleBodyLoader({
  ticketId,
  articleId,
}: {
  ticketId: number;
  articleId: number;
}) {
  const bodyQ = useQuery({
    queryKey: ["tickets", ticketId, "articles", articleId, "body"],
    queryFn: () => api.getArticleBody(ticketId, articleId),
  });

  if (bodyQ.isLoading) {
    return (
      <div className="flex justify-center py-4">
        <Spinner />
      </div>
    );
  }
  if (bodyQ.isError || !bodyQ.data) {
    return <p className="text-xs text-danger">Failed to load body</p>;
  }
  return (
    <ArticleBodyRenderer body={bodyQ.data.body} isHtml={bodyQ.data.is_html} />
  );
}

function AttachmentList({
  ticketId,
  articleId,
}: {
  ticketId: number;
  articleId: number;
}) {
  const { t } = useTranslation();
  const attQ = useQuery({
    queryKey: ["tickets", ticketId, "articles", articleId, "attachments"],
    queryFn: () => api.listAttachments(ticketId, articleId),
  });

  if (attQ.isLoading) return null;
  const items = attQ.data ?? [];
  if (items.length === 0) return null;

  return (
    <div data-testid="attachment-list">
      <h4 className="mb-1 text-xs font-semibold text-muted">
        {t("ticket.attachments")}
      </h4>
      <ul className="space-y-1">
        {items.map((a) => (
          <li key={a.id}>
            <a
              className="inline-flex items-center gap-2 text-xs text-accent hover:underline"
              href={api.attachmentDownloadUrl(ticketId, articleId, a.id, true)}
              download={a.filename ?? undefined}
              data-testid={`attachment-${a.id}`}
            >
              <span>{a.filename || `attachment-${a.id}`}</span>
              <span className="text-muted">
                {formatBytes(a.content_size)} · {a.content_type || "—"}
              </span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
