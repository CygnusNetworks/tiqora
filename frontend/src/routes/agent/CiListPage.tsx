import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CiStateBadge } from "@/components/itsm/CiStateBadge";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { ciDetailHref } from "@/lib/itsmRoutes";
import { itsmApi, itsmErrorKey, itsmKeys, type CiListQuery, type CiSource } from "@/lib/itsmApi";

const PAGE = 25;
const DEBOUNCE_MS = 300;
/** Attribute values are searched from this many characters on; shorter input only matches number and name. */
export const ATTRIBUTE_SEARCH_MIN = 3;

const FIELD_CLASS =
  "w-full rounded-lg border border-hairline bg-surface px-3 py-1.5 text-sm text-ink focus-visible:border-accent focus-visible:outline-none";

/** `/agent/itsm/cis`: search and browse the Znuny configuration items (Mode A, read-only). */
export function CiListPage() {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [classKey, setClassKey] = useState("");
  const [depl, setDepl] = useState("");
  const [inci, setInci] = useState("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(text.trim());
      setOffset(0);
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text]);

  const catalogQ = useQuery({
    queryKey: itsmKeys.catalog(),
    queryFn: ({ signal }) => itsmApi.catalog(signal),
    retry: false,
    staleTime: 60_000,
  });
  const query: CiListQuery = {
    q: q || undefined,
    class: classKey ? [classKey] : undefined,
    depl: depl ? [depl] : undefined,
    inci: inci ? [inci] : undefined,
    limit: PAGE,
    offset,
  };
  const pageQ = useQuery({
    queryKey: itsmKeys.cis(query),
    queryFn: ({ signal }) => itsmApi.cis(query, signal),
    retry: false,
    placeholderData: (prev) => prev,
  });

  const items = pageQ.data?.items ?? [];
  const total = pageQ.data?.total ?? 0;
  const catalog = catalogQ.data;
  const shortSearch = text.trim().length > 0 && text.trim().length < ATTRIBUTE_SEARCH_MIN;

  const filter = (setter: (v: string) => void) => (v: string) => {
    setter(v);
    setOffset(0);
  };

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6" data-testid="ci-list-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("itsm.ci.title")}</h1>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted" htmlFor="ci-list-search">
            {t("itsm.ci.searchLabel")}
          </label>
          <input
            id="ci-list-search"
            data-testid="ci-list-search"
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={t("itsm.ci.searchPlaceholder")}
            className={FIELD_CLASS}
          />
        </div>
        <FilterSelect
          label={t("itsm.ci.classLabel")}
          testId="ci-list-class"
          all={t("itsm.ci.allClasses")}
          value={classKey}
          onChange={filter(setClassKey)}
          options={(catalog?.classes ?? []).map((c) => ({ value: c.key, label: c.name }))}
        />
        <FilterSelect
          label={t("itsm.ci.deploymentLabel")}
          testId="ci-list-depl"
          all={t("itsm.ci.allDeployment")}
          value={depl}
          onChange={filter(setDepl)}
          options={(catalog?.deployment_states ?? []).map((s) => ({ value: s.key, label: s.name }))}
        />
        <FilterSelect
          label={t("itsm.ci.incidentLabel")}
          testId="ci-list-inci"
          all={t("itsm.ci.allIncident")}
          value={inci}
          onChange={filter(setInci)}
          options={(catalog?.incident_states ?? []).map((s) => ({ value: s.key, label: s.name }))}
        />
      </div>
      {shortSearch && (
        <p className="text-xs text-muted" data-testid="ci-list-hint">
          {t("itsm.ci.searchHint")}
        </p>
      )}

      {pageQ.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : pageQ.isError ? (
        <p
          className="rounded-lg border border-hairline bg-surface px-4 py-6 text-center text-sm text-danger"
          role="alert"
          data-testid="ci-list-error"
        >
          {t(itsmErrorKey(pageQ.error))}
        </p>
      ) : items.length === 0 ? (
        <p
          className="rounded-lg border border-hairline bg-surface px-4 py-8 text-center text-sm text-muted"
          data-testid="ci-list-empty"
        >
          {t("itsm.ci.empty")}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
          <table className="w-full text-sm" data-testid="ci-list-table">
            <thead className="border-b border-hairline text-left text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">{t("itsm.ci.colNumber")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.ci.colName")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.ci.colClass")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.ci.colDeployment")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.ci.colIncident")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {items.map((ci) => (
                <tr key={ci.ref} data-testid={`ci-row-${ci.id}`}>
                  <td className="px-3 py-2 font-mono text-xs">
                    <a href={ciDetailHref(ci.source as CiSource, ci.id)} className="text-accent hover:underline">
                      {ci.number}
                    </a>
                  </td>
                  <td className="px-3 py-2 text-ink">{ci.name}</td>
                  <td className="px-3 py-2 text-muted">{ci.class_name}</td>
                  <td className="px-3 py-2">
                    <CiStateBadge state={ci.deployment_state} />
                  </td>
                  <td className="px-3 py-2">
                    <CiStateBadge state={ci.incident_state} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pageQ.data?.truncated && (
        <p className="text-xs text-muted" data-testid="ci-list-truncated">
          {t("itsm.ci.truncated")}
        </p>
      )}

      {items.length > 0 && (
        <div className="flex items-center justify-between gap-3 text-xs text-muted" data-testid="ci-list-paging">
          <span data-testid="ci-list-range">
            {pageQ.data?.truncated
              ? t("itsm.ci.total", { count: items.length })
              : t("itsm.ci.range", { from: offset + 1, to: offset + items.length, total })}
          </span>
          <div className="flex gap-2">
            <Button size="sm" data-testid="ci-list-prev" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              {t("itsm.ci.prev")}
            </Button>
            <Button
              size="sm"
              data-testid="ci-list-next"
              disabled={offset + items.length >= total}
              onClick={() => setOffset(offset + PAGE)}
            >
              {t("itsm.ci.next")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function FilterSelect({
  label,
  testId,
  all,
  value,
  onChange,
  options,
}: {
  label: string;
  testId: string;
  all: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div>
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      <SelectField items={[{ value: "", label: all }, ...options]} value={value} onChange={onChange} testId={testId} />
    </div>
  );
}
