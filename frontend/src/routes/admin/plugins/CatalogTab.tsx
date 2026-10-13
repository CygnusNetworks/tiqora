import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { SoftChip } from "@/components/ui/StatusChip";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { pluginsApi, type CatalogEntry } from "@/lib/pluginsApi";

const SEARCH_DEBOUNCE_MS = 300;
const ALL = "";

type EntryAction = "install" | "update" | "installed" | "image" | "incompatible";

function entryAction(e: CatalogEntry): EntryAction {
  if (e.image_plugin) return "image";
  if (e.latest_compatible == null) return "incompatible";
  if (e.update_available) return "update";
  if (e.installed_version != null) return "installed";
  return "install";
}

const entryKey = (e: CatalogEntry) => `${e.repository_id}:${e.plugin_id}`;

/** "Verfügbar": the stored indexes of all package sources, with search and category filter. */
export function CatalogTab({
  runtimeInstall,
  downloading,
  onDownload,
}: {
  runtimeInstall: boolean;
  /** A download job runs (started here or from the "Installiert" tab). */
  downloading: boolean;
  onDownload: (repositoryId: number, pluginId: string, version: string) => void;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState(ALL);
  const [repositoryId, setRepositoryId] = useState<number | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setQ(search.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [search]);

  const reposQ = useQuery({
    queryKey: ["admin", "plugins", "repositories"],
    queryFn: ({ signal }) => pluginsApi.repositories(signal),
  });
  const params = {
    ...(q ? { q } : {}),
    ...(category ? { category } : {}),
    ...(repositoryId != null ? { repositoryId } : {}),
  };
  const catalogQ = useQuery({
    queryKey: ["admin", "plugins", "catalog", params],
    queryFn: ({ signal }) => pluginsApi.catalog(params, signal),
    placeholderData: (prev) => prev,
  });

  // Install / update = download the package from the repository (a job on the page, which then
  // opens the install dialog).
  const start = (e: CatalogEntry) => {
    if (!e.latest_compatible) return;
    onDownload(e.repository_id, e.plugin_id, e.latest_compatible);
  };

  const repos = reposQ.data ?? [];
  const noRepositories = reposQ.isSuccess && repos.length === 0;
  const data = catalogQ.data;
  const categoryItems = [
    { value: ALL, label: t("admin.plugins.catalog.allCategories") },
    ...(data?.categories ?? []).map((c) => ({ value: c, label: c })),
  ];
  const repoItems = [
    { value: -1, label: t("admin.plugins.catalog.allRepositories") },
    ...repos.map((r) => ({ value: r.id, label: r.name })),
  ];

  const label = (a: EntryAction) =>
    a === "install"
      ? t("admin.plugins.catalog.install")
      : a === "update"
        ? t("admin.plugins.update.action")
        : a === "installed"
          ? t("admin.plugins.catalog.installed")
          : a === "image"
            ? t("admin.plugins.source.image")
            : t("admin.plugins.catalog.incompatible");

  return (
    <div className="space-y-3" data-testid="plugin-catalog">
      {noRepositories ? (
        <p className="rounded-xl border border-hairline bg-surface p-4 text-sm text-muted" data-testid="catalog-no-repositories">
          {t("admin.plugins.catalog.noRepositories")}
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-3">
            <label className="min-w-[12rem] flex-1 text-sm text-ink">
              <span className="mb-1 block text-xs font-medium text-muted">{t("admin.plugins.catalog.search")}</span>
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
                data-testid="catalog-search"
              />
            </label>
            <div className="min-w-[10rem] text-sm">
              <span className="mb-1 block text-xs font-medium text-muted">{t("admin.plugins.catalog.category")}</span>
              <SelectField
                items={categoryItems}
                value={category}
                onChange={setCategory}
                testId="catalog-category"
                aria-label={t("admin.plugins.catalog.category")}
              />
            </div>
            {repos.length > 1 && (
              <div className="min-w-[10rem] text-sm">
                <span className="mb-1 block text-xs font-medium text-muted">{t("admin.plugins.catalog.repository")}</span>
                <SelectField
                  items={repoItems}
                  value={repositoryId ?? -1}
                  onChange={(v) => setRepositoryId(v === -1 ? null : v)}
                  testId="catalog-repository"
                  aria-label={t("admin.plugins.catalog.repository")}
                />
              </div>
            )}
          </div>

          {catalogQ.isLoading && <Spinner />}
          {catalogQ.isError && (
            <p role="alert" className="text-sm text-danger">
              {apiErrorMessage(catalogQ.error, t("admin.plugins.catalog.loadError"))}
            </p>
          )}
          {data && data.entries.length === 0 && (
            <p className="rounded-xl border border-hairline bg-surface p-4 text-sm text-muted" data-testid="catalog-empty">
              {t("admin.plugins.catalog.empty")}
            </p>
          )}
          {data && data.entries.length > 0 && (
            <ul className="list-none space-y-3">
              {data.entries.map((e) => {
                const action = entryAction(e);
                const canClick = (action === "install" || action === "update") && runtimeInstall && !downloading;
                const version = e.latest_compatible ?? e.latest_version;
                return (
                  <li
                    key={entryKey(e)}
                    className="rounded-xl border border-hairline bg-surface p-4"
                    data-testid={`catalog-entry-${e.plugin_id}`}
                  >
                    <div className="flex flex-wrap items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <h2 className="font-display text-base font-semibold text-ink">{e.name}</h2>
                          {e.publisher_verified && (
                            <SoftChip color="var(--color-accent)">{t("admin.plugins.trust.verified_publisher")}</SoftChip>
                          )}
                        </div>
                        <p className="mt-0.5 text-xs text-muted">
                          {t("admin.plugins.version", { version })}
                          {e.publisher ? ` · ${t("admin.plugins.publisher", { publisher: e.publisher })}` : ""}
                          {" · "}
                          {e.repository_name}
                        </p>
                        {e.description && <p className="mt-1.5 text-sm text-ink">{e.description}</p>}
                        {e.installed_version && (
                          <p className="mt-1 text-xs text-muted">
                            {t("admin.plugins.catalog.installedVersion", { version: e.installed_version })}
                          </p>
                        )}
                        {e.homepage && (
                          <a
                            href={e.homepage}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="mt-1 inline-block text-xs text-accent underline"
                          >
                            {t("admin.plugins.catalog.homepage")}
                          </a>
                        )}
                      </div>
                      <Button
                        size="sm"
                        variant={action === "install" || action === "update" ? "primary" : "secondary"}
                        disabled={!canClick}
                        onClick={() => start(e)}
                        data-testid={`catalog-action-${e.plugin_id}`}
                      >
                        {label(action)}
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
