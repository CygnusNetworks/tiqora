import { useMemo } from "react";
import { useParams, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { portalApi } from "@/lib/portalApi";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

export function KbArticlePage() {
  const { t } = useTranslation();
  const { slug } = useParams({ from: "/portal/kb/$slug" });

  const articleQ = useQuery({
    queryKey: ["portal", "kb", "article", slug],
    queryFn: () => portalApi.portalGetKbArticle(slug),
  });

  const html = useMemo(() => {
    if (!articleQ.data) return "";
    const raw = marked.parse(articleQ.data.content_md, { async: false }) as string;
    return DOMPurify.sanitize(raw);
  }, [articleQ.data]);

  if (articleQ.isLoading) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  if (articleQ.isError || !articleQ.data) {
    return (
      <div className="space-y-2" data-testid="portal-kb-article-error">
        <p className="text-sm text-danger">{t("portal.kb.loadError")}</p>
        <Link to="/portal/kb" className="text-sm text-accent hover:underline">
          {t("portal.kb.backToSearch")}
        </Link>
      </div>
    );
  }

  const article = articleQ.data;

  return (
    <article className="space-y-4" data-testid="portal-kb-article-page">
      <Link to="/portal/kb" className="text-sm text-accent hover:underline">
        {t("portal.kb.backToSearch")}
      </Link>
      <header className="space-y-1.5">
        <h1 className="font-display text-xl font-semibold text-ink">{article.title}</h1>
        {article.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {article.tags.map((tag) => (
              <Badge key={tag} tone="muted">
                {tag}
              </Badge>
            ))}
          </div>
        )}
      </header>
      <div
        className="prose-portal max-w-none text-sm text-ink"
        data-testid="portal-kb-article-body"
        // content_md is customer-facing, published KB markdown, rendered via
        // marked and sanitised with DOMPurify before insertion.
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </article>
  );
}
