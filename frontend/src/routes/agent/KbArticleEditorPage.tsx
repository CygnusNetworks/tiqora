import { useEffect, useRef, useState, type FormEvent } from "react";
import { useParams, useNavigate, Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Dialog } from "@/components/ui/Dialog";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { ChevronDownIcon } from "@/components/ui/icons";
import { MarkdownView } from "@/components/kb/MarkdownView";
import { KbAttachments } from "@/components/kb/KbAttachments";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { slugify } from "@/lib/slug";
import { cn } from "@/lib/cn";

const STATES = ["draft", "review", "published", "archived"] as const;
const LANGUAGES = ["en", "de"] as const;

const inputClass =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";

const selectTriggerClass =
  "flex w-full items-center justify-between gap-2 rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-left text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

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
  // On create the slug is optional (backend derives it from the title). We
  // keep the field hidden behind a toggle and only send a slug the user chose.
  const [editSlug, setEditSlug] = useState(false);

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
    // Slug is optional on create (derived from the title server-side).
    if (!form.title.trim() || form.categoryId == null || !form.contentMd.trim()) {
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
          // Only send a slug if the author explicitly set one; otherwise let
          // the backend derive it from the title.
          ...(editSlug && form.slug.trim() ? { slug: form.slug.trim() } : {}),
          category_id: form.categoryId,
          language: form.language,
          state: form.state,
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

  const categoryItems: SelectMenuItem<number>[] = (categoriesQ.data ?? []).map((c) => ({
    value: c.id,
    label: c.name,
  }));
  const languageItems: SelectMenuItem<string>[] = LANGUAGES.map((l) => ({
    value: l,
    label: l.toUpperCase(),
  }));
  const stateItems: SelectMenuItem<string>[] = STATES.map((s) => ({
    value: s,
    label: t(`kb.state.${s}`),
  }));

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

      {categoriesQ.data && categoriesQ.data.length === 0 && (
        <div
          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-escalation/40 bg-escalation/10 px-4 py-3 text-sm text-ink"
          data-testid="kb-editor-no-categories"
        >
          <span>{t("kb.editorNoCategories")}</span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void navigate({ to: "/agent/kb/categories", search: { new: true } })}
          >
            {t("kb.createFirstCategory")}
          </Button>
        </div>
      )}

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
          <div className="block text-sm">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-muted">{t("kb.field.slug")}</span>
              {!isEdit && (
                <button
                  type="button"
                  data-testid="kb-form-slug-toggle"
                  onClick={() => setEditSlug((v) => !v)}
                  className="text-xs text-accent hover:underline"
                >
                  {editSlug ? t("kb.slug.useAuto") : t("kb.slug.editSlug")}
                </button>
              )}
            </div>
            {isEdit || editSlug ? (
              <input
                data-testid="kb-form-slug"
                readOnly={isEdit}
                value={isEdit ? form.slug : form.slug || slugify(form.title)}
                onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value }))}
                className={cn(inputClass, isEdit && "opacity-70")}
              />
            ) : (
              <p
                data-testid="kb-form-slug-preview"
                className="truncate rounded-md border border-hairline bg-surface-subtle px-3 py-2 font-mono text-sm text-muted"
                title={slugify(form.title)}
              >
                {slugify(form.title) || t("kb.slug.autoPlaceholder")}
              </p>
            )}
          </div>
          <label className="block text-sm">
            <span className="mb-1 flex items-center gap-1.5 text-muted">
              {t("kb.field.category")}
              <HelpPopover title={t("kb.field.category")} testId="kb-help-category">
                {t("kb.help.category")}
              </HelpPopover>
            </span>
            <SelectMenu
              items={categoryItems}
              value={form.categoryId ?? undefined}
              onSelect={(v) => setForm((f) => ({ ...f, categoryId: v }))}
              loading={categoriesQ.isLoading}
              placeholder={t("kb.field.category")}
              panelTestId="kb-form-category-panel"
              trigger={({ open, ref, toggleProps }) => (
                <button
                  ref={ref}
                  type="button"
                  data-testid="kb-form-category"
                  {...toggleProps}
                  className={selectTriggerClass}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {categoryItems.find((i) => i.value === form.categoryId)?.label ??
                      t("kb.field.category")}
                  </span>
                  <ChevronDownIcon
                    className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-180")}
                  />
                </button>
              )}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 flex items-center gap-1.5 text-muted">
              {t("kb.field.language")}
              <HelpPopover title={t("kb.field.language")} testId="kb-help-language">
                {t("kb.help.language")}
              </HelpPopover>
            </span>
            <SelectMenu
              items={languageItems}
              value={form.language}
              onSelect={(v) => setForm((f) => ({ ...f, language: v }))}
              panelTestId="kb-form-language-panel"
              trigger={({ open, ref, toggleProps }) => (
                <button
                  ref={ref}
                  type="button"
                  data-testid="kb-form-language"
                  {...toggleProps}
                  className={selectTriggerClass}
                >
                  <span>{form.language.toUpperCase()}</span>
                  <ChevronDownIcon
                    className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-180")}
                  />
                </button>
              )}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 flex items-center gap-1.5 text-muted">
              {t("kb.field.state")}
              <HelpPopover title={t("kb.field.state")} testId="kb-help-state">
                {t("kb.help.state")}
              </HelpPopover>
            </span>
            <SelectMenu
              items={stateItems}
              value={form.state}
              onSelect={(v) => setForm((f) => ({ ...f, state: v }))}
              panelTestId="kb-form-state-panel"
              trigger={({ open, ref, toggleProps }) => (
                <button
                  ref={ref}
                  type="button"
                  data-testid="kb-form-state"
                  {...toggleProps}
                  className={selectTriggerClass}
                >
                  <span>{t(`kb.state.${form.state}`)}</span>
                  <ChevronDownIcon
                    className={cn("shrink-0 text-muted transition-transform duration-150", open && "rotate-180")}
                  />
                </button>
              )}
            />
          </label>
          <label className="block text-sm sm:col-span-2">
            <span className="mb-1 flex items-center gap-1.5 text-muted">
              {t("kb.field.tags")}
              <HelpPopover title={t("kb.field.tags")} testId="kb-help-tags">
                {t("kb.help.tags")}
              </HelpPopover>
            </span>
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

        {isEdit && articleId != null && <KbAttachments articleId={articleId} />}

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
