import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { CategoryTree } from "@/components/agent/CategoryTree";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { ChevronDownIcon } from "@/components/ui/icons";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";

const STATE_FILTERS = ["all", "draft", "review", "published", "archived"] as const;
type StateFilter = (typeof STATE_FILTERS)[number];

const STATE_TONE: Record<string, "muted" | "accent" | "success" | "warn"> = {
  draft: "muted",
  review: "warn",
  published: "success",
  archived: "muted",
};

export type KbSearch = { category_id?: number; state?: StateFilter };

export function KbPage() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate({ from: "/agent/kb" });
  const search = useSearch({ from: "/agent/kb" }) as KbSearch;
  const [drawerOpen, setDrawerOpen] = useState(false);

  const categoryId = search.category_id ?? null;
  const state = search.state ?? "all";

  const setSearch = (patch: Partial<KbSearch>) => {
    void navigate({
      search: (prev: KbSearch) => ({ ...prev, ...patch }),
      replace: true,
    });
  };

  const categoriesQ = useQuery({
    queryKey: ["kb", "categories"],
    queryFn: () => api.listKbCategories(),
  });

  // Unfiltered fetch to derive per-category counts for the sidebar tree.
  const allArticlesQ = useQuery({
    queryKey: ["kb", "articles", "all"],
    queryFn: () => api.listKbArticles(),
  });

  const articlesQ = useQuery({
    queryKey: ["kb", "articles", { categoryId, state }],
    queryFn: () =>
      api.listKbArticles({
        category_id: categoryId ?? undefined,
        state: state === "all" ? undefined : state,
      }),
  });

  const counts = useMemo(() => {
    const out: Record<number, number> = {};
    for (const a of allArticlesQ.data ?? []) {
      out[a.category_id] = (out[a.category_id] ?? 0) + 1;
    }
    return out;
  }, [allArticlesQ.data]);

  const categoriesById = useMemo(
    () => new Map((categoriesQ.data ?? []).map((c) => [c.id, c])),
    [categoriesQ.data],
  );

  const stateFilterItems: SelectMenuItem<StateFilter>[] = STATE_FILTERS.map((s) => ({
    value: s,
    label: t(`kb.state.${s}`),
  }));

  const sidebarBody = categoriesQ.isLoading ? (
    <div className="flex justify-center py-6">
      <Spinner />
    </div>
  ) : (
    <CategoryTree
      categories={categoriesQ.data ?? []}
      selectedId={categoryId}
      counts={counts}
      onSelect={(id) => {
        setSearch({ category_id: id ?? undefined });
        setDrawerOpen(false);
      }}
    />
  );

  return (
    <div className="relative flex min-h-0 flex-1" data-testid="kb-page">
      <aside className="hidden w-56 shrink-0 overflow-y-auto border-r border-hairline bg-surface p-2 md:block lg:w-64">
        <h2 className="mb-2 px-2 text-xs font-semibold uppercase tracking-wide text-muted">
          {t("kb.sidebar")}
        </h2>
        {sidebarBody}
      </aside>

      {drawerOpen && (
        <div className="fixed inset-0 z-30 md:hidden">
          <button
            type="button"
            aria-label={t("common.back")}
            className="absolute inset-0 bg-black/40"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 w-64 overflow-y-auto border-r border-hairline bg-surface p-2 shadow-xl">
            <div className="mb-2 flex items-center justify-between px-2">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
                {t("kb.sidebar")}
              </h2>
              <Button variant="ghost" size="sm" onClick={() => setDrawerOpen(false)}>
                ✕
              </Button>
            </div>
            {sidebarBody}
          </div>
        </div>
      )}

      <div className="min-w-0 flex-1 space-y-3 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            className="md:hidden"
            onClick={() => setDrawerOpen(true)}
            data-testid="kb-drawer-toggle"
          >
            {t("kb.sidebar")}
          </Button>
          <h1 className="font-display text-lg font-semibold text-ink">
            {t("kb.title")}
          </h1>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <SelectMenu
              items={stateFilterItems}
              value={state}
              onSelect={(v) => setSearch({ state: v })}
              panelTestId="kb-state-filter-panel"
              trigger={({ open, ref, toggleProps }) => (
                <button
                  ref={ref}
                  type="button"
                  data-testid="kb-state-filter"
                  {...toggleProps}
                  className="flex min-w-[9rem] items-center justify-between gap-2 rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                >
                  <span>{t(`kb.state.${state}`)}</span>
                  <ChevronDownIcon
                    className={cn("text-muted transition-transform duration-150", open && "rotate-180")}
                  />
                </button>
              )}
            />
            <Button
              variant="primary"
              size="sm"
              data-testid="kb-new-article"
              onClick={() => void navigate({ to: "/agent/kb/new" })}
            >
              {t("kb.newArticle")}
            </Button>
          </div>
        </div>

        {articlesQ.isLoading ? (
          <div className="flex justify-center py-8">
            <Spinner />
          </div>
        ) : (articlesQ.data ?? []).length === 0 ? (
          <div
            className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center"
            data-testid="kb-empty"
          >
            <p className="text-sm font-medium text-ink">{t("kb.empty")}</p>
          </div>
        ) : (
          <ul className="space-y-2" data-testid="kb-article-list">
            {(articlesQ.data ?? []).map((a) => (
              <li key={a.id}>
                <Link
                  to="/agent/kb/$articleId"
                  params={{ articleId: String(a.id) }}
                  data-testid={`kb-article-${a.id}`}
                  className="block rounded-lg border border-hairline bg-surface p-3 transition-colors duration-100 hover:border-accent/60 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-muted">#{a.id}</span>
                    <Badge tone={STATE_TONE[a.state] ?? "muted"}>
                      {t(`kb.state.${a.state}`, a.state)}
                    </Badge>
                    <span className="text-xs uppercase text-muted">
                      {a.language}
                    </span>
                    {categoriesById.get(a.category_id) && (
                      <span className="text-xs text-muted">
                        {categoriesById.get(a.category_id)!.name}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm font-medium text-ink">{a.title}</p>
                  <p className="mt-0.5 text-xs text-muted">
                    {t("kb.lastChanged", {
                      date: formatDateTime(a.change_time, i18n.language),
                    })}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
