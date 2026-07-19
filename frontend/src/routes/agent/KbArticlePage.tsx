import { useState } from "react";
import { useParams, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { ArticleVersionOut } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { MarkdownView } from "@/components/kb/MarkdownView";
import { categoryBreadcrumbs } from "@/components/agent/CategoryTree";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";

const STATE_TONE: Record<string, "muted" | "accent" | "success" | "warn"> = {
  draft: "muted",
  review: "warn",
  published: "success",
  archived: "muted",
};

export function KbArticlePage() {
  const { t, i18n } = useTranslation();
  const { articleId } = useParams({ from: "/agent/kb/$articleId" });
  const id = Number(articleId);
  const [viewingVersion, setViewingVersion] = useState<ArticleVersionOut | null>(
    null,
  );

  const articleQ = useQuery({
    queryKey: ["kb", "article", id],
    queryFn: () => api.getKbArticle(id),
  });

  const categoriesQ = useQuery({
    queryKey: ["kb", "categories"],
    queryFn: () => api.listKbCategories(),
  });

  const versionsQ = useQuery({
    queryKey: ["kb", "article", id, "versions"],
    queryFn: () => api.listKbArticleVersions(id),
  });

  if (articleQ.isLoading) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  if (articleQ.isError || !articleQ.data) {
    return (
      <div className="space-y-2 p-4" data-testid="kb-article-error">
        <p className="text-sm text-danger">{t("kb.loadError")}</p>
        <Link to="/agent/kb" className="text-sm text-accent hover:underline">
          {t("kb.backToBrowse")}
        </Link>
      </div>
    );
  }

  const article = articleQ.data;
  const breadcrumbs = categoryBreadcrumbs(categoriesQ.data ?? [], article.category_id);
  const displayed = viewingVersion ?? article;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3 lg:flex-row" data-testid="kb-article-page">
      <article className="min-w-0 flex-1 space-y-4">
        <nav aria-label={t("kb.breadcrumbs")} className="flex flex-wrap items-center gap-1 text-xs text-muted">
          <Link to="/agent/kb" className="hover:text-accent hover:underline">
            {t("kb.title")}
          </Link>
          {breadcrumbs.map((c) => (
            <span key={c.id} className="flex items-center gap-1">
              <span aria-hidden="true">/</span>
              <Link
                to="/agent/kb"
                search={{ category_id: c.id }}
                className="hover:text-accent hover:underline"
              >
                {c.name}
              </Link>
            </span>
          ))}
        </nav>

        <header className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-display text-xl font-semibold text-ink">
              {displayed.title}
            </h1>
            <Badge tone={STATE_TONE[article.state] ?? "muted"}>
              {t(`kb.state.${article.state}`, article.state)}
            </Badge>
            <span className="font-mono text-xs text-muted">
              {t("kb.version", { version: displayed.version })}
            </span>
          </div>
          {(article.tags ?? []).length > 0 && (
            <div className="flex flex-wrap gap-1">
              {(article.tags ?? []).map((tag) => (
                <Badge key={tag} tone="muted">
                  {tag}
                </Badge>
              ))}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to="/agent/kb/$articleId/edit"
              params={{ articleId: String(article.id) }}
              data-testid="kb-edit-link"
              className="text-sm text-accent hover:underline"
            >
              {t("kb.edit")}
            </Link>
          </div>
        </header>

        {viewingVersion && (
          <div
            className="flex flex-wrap items-center justify-between gap-2 rounded border border-escalation/40 bg-escalation/10 px-3 py-2 text-xs text-escalation"
            data-testid="kb-version-banner"
          >
            <span>
              {t("kb.viewingVersion", { version: viewingVersion.version })}
            </span>
            <Button size="sm" variant="secondary" onClick={() => setViewingVersion(null)}>
              {t("kb.viewCurrent")}
            </Button>
          </div>
        )}

        <MarkdownView markdown={displayed.content_md} data-testid="kb-article-body" />
      </article>

      <aside
        className="w-full shrink-0 space-y-2 border-t border-hairline pt-3 lg:w-72 lg:border-l lg:border-t-0 lg:pl-3 lg:pt-0"
        data-testid="kb-version-history"
      >
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
          {t("kb.versionHistory")}
        </h2>
        {versionsQ.isLoading ? (
          <div className="flex justify-center py-4">
            <Spinner />
          </div>
        ) : (versionsQ.data ?? []).length === 0 ? (
          <p className="text-xs text-muted">{t("kb.noVersions")}</p>
        ) : (
          <ul className="space-y-1">
            {(versionsQ.data ?? [])
              .slice()
              .sort((a, b) => b.version - a.version)
              .map((v) => (
                <li key={v.id}>
                  <button
                    type="button"
                    data-testid={`kb-version-${v.version}`}
                    onClick={() => setViewingVersion(v)}
                    className={cn(
                      "flex w-full flex-col items-start rounded px-2 py-1.5 text-left text-xs transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
                      viewingVersion?.id === v.id &&
                        "bg-surface-subtle font-medium text-accent",
                    )}
                  >
                    <span>{t("kb.version", { version: v.version })}</span>
                    <span className="text-muted">
                      {formatDateTime(v.changed_at, i18n.language)}
                    </span>
                  </button>
                </li>
              ))}
          </ul>
        )}
      </aside>
    </div>
  );
}
