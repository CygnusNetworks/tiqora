import { useEffect, useRef, useState, type FormEvent } from "react";
import { useParams, useNavigate, Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Dialog } from "@/components/ui/Dialog";
import { MarkdownView } from "@/components/kb/MarkdownView";
import { cn } from "@/lib/cn";

const STATES = ["draft", "review", "published", "archived"] as const;
const LANGUAGES = ["en", "de"] as const;

const inputClass =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";

type FormState = {
  title: string;
  slug: string;
  categoryId: number | null;
  language: string;
  state: string;
  tags: string;
  contentMd: string;
};

const EMPTY_FORM: FormState = {
  title: "",
  slug: "",
  categoryId: null,
  language: "en",
  state: "draft",
  tags: "",
  contentMd: "",
};

function KbArticleEditor({ articleId }: { articleId?: number }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isEdit = articleId != null;

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [currentState, setCurrentState] = useState<string | null>(null);

  const categoriesQ = useQuery({
    queryKey: ["kb", "categories"],
    queryFn: () => api.listKbCategories(),
  });

  const articleQ = useQuery({
    queryKey: ["kb", "article", articleId],
    queryFn: () => api.getKbArticle(articleId!),
    enabled: isEdit,
  });

  useEffect(() => {
    if (!isEdit || !articleQ.data) return;
    const a = articleQ.data;
    setForm({
      title: a.title,
      slug: a.slug,
      categoryId: a.category_id,
      language: a.language,
      state: a.state,
      tags: (a.tags ?? []).join(", "),
      contentMd: a.content_md,
    });
    setCurrentState(a.state);
  }, [isEdit, articleQ.data]);

  const didDefaultCategory = useRef(false);
  useEffect(() => {
    if (isEdit || didDefaultCategory.current) return;
    if (categoriesQ.data && categoriesQ.data.length > 0) {
      didDefaultCategory.current = true;
      setForm((f) => ({ ...f, categoryId: categoriesQ.data[0].id }));
    }
  }, [isEdit, categoriesQ.data]);

  const tagsArray = () =>
    form.tags
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!form.title.trim() || !form.slug.trim() || form.categoryId == null || !form.contentMd.trim()) {
      setError(t("kb.validationError"));
      return;
    }
    setSubmitting(true);
    try {
      if (isEdit) {
        // ArticleUpdateIn has no `slug` field — the API does not support
        // renaming a slug after creation, so it's read-only here.
        await api.updateKbArticle(articleId!, {
          title: form.title,
          category_id: form.categoryId,
          language: form.language,
          state: form.state,
          tags: tagsArray(),
          content_md: form.contentMd,
        });
        await queryClient.invalidateQueries({ queryKey: ["kb"] });
        await navigate({ to: "/agent/kb/$articleId", params: { articleId: String(articleId) } });
      } else {
        const created = await api.createKbArticle({
          title: form.title,
          slug: form.slug,
          category_id: form.categoryId,
          language: form.language,
          tags: tagsArray(),
          content_md: form.contentMd,
        });
        await queryClient.invalidateQueries({ queryKey: ["kb"] });
        await navigate({
          to: "/agent/kb/$articleId",
          params: { articleId: String(created.id) },
        });
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(t("kb.submitError"));
      } else {
        throw err;
      }
    } finally {
      setSubmitting(false);
    }
  };

  const onPublish = async () => {
    if (!isEdit) return;
    setPublishing(true);
    try {
      const updated = await api.publishKbArticle(articleId!);
      setCurrentState(updated.state);
      setForm((f) => ({ ...f, state: updated.state }));
      await queryClient.invalidateQueries({ queryKey: ["kb"] });
      setPublishOpen(false);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(t("kb.publishError"));
      } else {
        throw err;
      }
    } finally {
      setPublishing(false);
    }
  };

  if (isEdit && articleQ.isLoading) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 p-3" data-testid="kb-editor-page">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-display text-xl font-semibold text-ink">
          {isEdit ? t("kb.editArticle") : t("kb.newArticle")}
        </h1>
        {isEdit && (
          <Button
            variant="primary"
            data-testid="kb-publish-button"
            onClick={() => setPublishOpen(true)}
            disabled={currentState === "published"}
          >
            {t("kb.publish")}
          </Button>
        )}
      </div>

      <form onSubmit={(e) => void onSubmit(e)} className="space-y-4">
        <div className="grid grid-cols-1 gap-3 rounded-lg border border-hairline bg-surface p-4 sm:grid-cols-2">
          <label className="block text-sm sm:col-span-2">
            <span className="mb-1 block text-muted">{t("kb.field.title")}</span>
            <input
              data-testid="kb-form-title"
              required
              value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              className={inputClass}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("kb.field.slug")}</span>
            <input
              data-testid="kb-form-slug"
              required
              readOnly={isEdit}
              value={form.slug}
              onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value }))}
              className={cn(inputClass, isEdit && "opacity-70")}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("kb.field.category")}</span>
            <select
              data-testid="kb-form-category"
              required
              value={form.categoryId ?? ""}
              onChange={(e) =>
                setForm((f) => ({ ...f, categoryId: Number(e.target.value) }))
              }
              className={inputClass}
            >
              <option value="" disabled>
                {t("kb.field.category")}
              </option>
              {(categoriesQ.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("kb.field.language")}</span>
            <select
              data-testid="kb-form-language"
              value={form.language}
              onChange={(e) => setForm((f) => ({ ...f, language: e.target.value }))}
              className={inputClass}
            >
              {LANGUAGES.map((l) => (
                <option key={l} value={l}>
                  {l.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-muted">{t("kb.field.state")}</span>
            <select
              data-testid="kb-form-state"
              value={form.state}
              onChange={(e) => setForm((f) => ({ ...f, state: e.target.value }))}
              disabled={!isEdit}
              className={inputClass}
            >
              {STATES.map((s) => (
                <option key={s} value={s}>
                  {t(`kb.state.${s}`)}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm sm:col-span-2">
            <span className="mb-1 block text-muted">{t("kb.field.tags")}</span>
            <input
              data-testid="kb-form-tags"
              value={form.tags}
              onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))}
              placeholder={t("kb.field.tagsPlaceholder")}
              className={inputClass}
            />
          </label>
        </div>

        <div className="grid min-h-[24rem] grid-cols-1 gap-3 lg:grid-cols-2">
          <div className="flex min-h-0 flex-col rounded-lg border border-hairline bg-surface p-3">
            <span className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
              {t("kb.field.content")}
            </span>
            <textarea
              data-testid="kb-form-content"
              required
              value={form.contentMd}
              onChange={(e) => setForm((f) => ({ ...f, contentMd: e.target.value }))}
              className="min-h-[20rem] flex-1 resize-y rounded-md border border-hairline bg-surface-subtle p-3 font-mono text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
            />
          </div>
          <div className="flex min-h-0 flex-col rounded-lg border border-hairline bg-surface p-3">
            <span className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
              {t("kb.field.preview")}
            </span>
            <div className="min-h-[20rem] flex-1 overflow-y-auto rounded-md border border-hairline bg-surface-subtle p-3">
              <MarkdownView markdown={form.contentMd} data-testid="kb-editor-preview" />
            </div>
          </div>
        </div>

        {error && (
          <p className="text-sm text-danger" data-testid="kb-form-error" role="alert">
            {error}
          </p>
        )}

        <div className="flex items-center gap-2">
          <Button
            type="submit"
            variant="primary"
            disabled={submitting}
            data-testid="kb-form-submit"
          >
            {submitting ? <Spinner /> : t("kb.save")}
          </Button>
          <Link
            to={isEdit ? "/agent/kb/$articleId" : "/agent/kb"}
            params={isEdit ? { articleId: String(articleId) } : undefined}
            className="text-sm text-muted hover:text-ink hover:underline"
          >
            {t("kb.cancel")}
          </Link>
        </div>
      </form>

      <Dialog
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        title={t("kb.publishConfirmTitle")}
      >
        <p className="mb-4">{t("kb.publishConfirmBody")}</p>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setPublishOpen(false)}>
            {t("kb.cancel")}
          </Button>
          <Button
            variant="primary"
            onClick={() => void onPublish()}
            disabled={publishing}
            data-testid="kb-publish-confirm"
          >
            {publishing ? <Spinner /> : t("kb.publish")}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

export function KbArticleNewPage() {
  return <KbArticleEditor />;
}

export function KbArticleEditPage() {
  const { articleId } = useParams({ from: "/agent/kb/$articleId/edit" });
  return <KbArticleEditor articleId={Number(articleId)} />;
}
