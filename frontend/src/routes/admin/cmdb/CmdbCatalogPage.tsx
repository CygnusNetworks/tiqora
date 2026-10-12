import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { STATE_FUNCTIONALITIES } from "@/components/cmdb/adminModel";
import { CmdbModeNotice } from "@/components/cmdb/CmdbModeNotice";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { apiErrorMessage } from "@/lib/avatarApi";
import type { CatalogItem } from "@/lib/cmdbApi";
import { cmdbAdminApi, type CatalogItemIn } from "@/lib/cmdbAdminApi";

const INPUT = "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-60";
const LABEL = "mb-1 block text-xs font-medium uppercase tracking-wide text-muted";
const KEY_PATTERN = /^[A-Za-z0-9_]{1,100}$/;

function ItemDialog({
  catalogClass,
  item,
  onClose,
  onSaved,
}: {
  catalogClass: string;
  item: CatalogItem | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const functionalities = STATE_FUNCTIONALITIES[catalogClass];
  const [name, setName] = useState(item?.name ?? "");
  const [key, setKey] = useState(item?.item_key ?? "");
  const [functionality, setFunctionality] = useState<string | null>(item?.functionality ?? null);
  const [position, setPosition] = useState(String(item?.position ?? 0));
  const [valid, setValid] = useState(item?.valid ?? true);
  const keyOk = KEY_PATTERN.test(key);
  const posNum = Number(position);
  const posOk = Number.isInteger(posNum) && posNum >= 0 && posNum <= 100000;

  const body: CatalogItemIn = {
    catalog_class: catalogClass,
    item_key: key,
    name: name.trim(),
    functionality: functionalities ? functionality : null,
    position: posOk ? posNum : 0,
    valid,
  };
  const saveM = useMutation({
    mutationFn: () => (item ? cmdbAdminApi.updateCatalogItem(item.id, body) : cmdbAdminApi.createCatalogItem(body)),
    onSuccess: onSaved,
  });
  const canSave = name.trim() !== "" && keyOk && posOk && (!functionalities || functionality !== null) && !saveM.isPending;

  return (
    <Dialog
      open
      onClose={onClose}
      title={item ? t("admin.cmdb.catalog.edit") : t("admin.cmdb.catalog.new")}
      description={catalogClass}
      size="md"
      footer={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" disabled={!canSave} onClick={() => saveM.mutate()} data-testid="cmdb-catalog-save">
            {t("common.save")}
          </Button>
        </>
      }
    >
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSave) saveM.mutate();
        }}
      >
        <div>
          <label className={LABEL} htmlFor="cmdb-catalog-name">{t("admin.cmdb.catalog.name")}</label>
          <input id="cmdb-catalog-name" data-testid="cmdb-catalog-name" className={INPUT} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className={LABEL} htmlFor="cmdb-catalog-key">{t("admin.cmdb.catalog.key")}</label>
          <input
            id="cmdb-catalog-key"
            data-testid="cmdb-catalog-key"
            className={`${INPUT} font-mono`}
            value={key}
            maxLength={100}
            readOnly={item !== null}
            disabled={item !== null}
            aria-invalid={key !== "" && !keyOk}
            onChange={(e) => setKey(e.target.value)}
          />
          <p className="mt-1 text-xs text-muted">{item ? t("admin.cmdb.catalog.keyLocked") : t("admin.cmdb.catalog.keyHelp")}</p>
        </div>
        {functionalities && (
          <div>
            <label className={LABEL} htmlFor="cmdb-catalog-functionality">{t("admin.cmdb.catalog.functionality")}</label>
            <SelectField<string>
              id="cmdb-catalog-functionality"
              testId="cmdb-catalog-functionality"
              items={functionalities.map((f) => ({ value: f, label: t(`admin.cmdb.catalog.functionalities.${f}`) }))}
              value={functionality}
              onChange={setFunctionality}
              placeholder={t("admin.cmdb.catalog.functionalityPlaceholder")}
            />
            <p className="mt-1 text-xs text-muted">{t("admin.cmdb.catalog.functionalityHelp")}</p>
          </div>
        )}
        <div>
          <label className={LABEL} htmlFor="cmdb-catalog-position">{t("admin.cmdb.catalog.position")}</label>
          <input id="cmdb-catalog-position" data-testid="cmdb-catalog-position" type="number" min={0} max={100000} className={INPUT} value={position} onChange={(e) => setPosition(e.target.value)} />
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-ink">{t("admin.cmdb.catalog.valid")}</span>
          <Switch checked={valid} onChange={setValid} aria-label={t("admin.cmdb.catalog.valid")} testId="cmdb-catalog-valid" />
        </div>
        {saveM.isError && (
          <p role="alert" className="text-sm text-danger" data-testid="cmdb-catalog-error">
            {apiErrorMessage(saveM.error, t("admin.cmdb.catalog.saveError"))}
          </p>
        )}
      </form>
    </Dialog>
  );
}

/** Admin editor of the general catalog (D6): classes on the left, their items on the right. */
export function CmdbCatalogPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<CatalogItem | "new" | null>(null);

  const statusQ = useQuery({ queryKey: ["admin", "cmdb", "status"], queryFn: () => cmdbAdminApi.status() });
  const active = statusQ.data?.mode === "tiqora";
  const classesQ = useQuery({ queryKey: ["admin", "cmdb", "catalog-classes"], queryFn: () => cmdbAdminApi.catalogClasses(), enabled: active });
  const classes = useMemo(() => classesQ.data ?? [], [classesQ.data]);
  useEffect(() => {
    if (selected === null && classes.length > 0) setSelected(classes[0]);
  }, [classes, selected]);
  const itemsQ = useQuery({
    queryKey: ["admin", "cmdb", "catalog", selected],
    queryFn: () => cmdbAdminApi.catalog(selected as string),
    enabled: active && selected !== null,
  });
  const items = [...(itemsQ.data ?? [])].sort((a, b) => a.position - b.position || a.name.localeCompare(b.name));
  const hasFunctionality = selected !== null && selected in STATE_FUNCTIONALITIES;

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 sm:p-6" data-testid="admin-cmdb-catalog-page">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">{t("admin.cmdb.catalog.title")}</h1>
          <p className="mt-1 text-xs text-muted">{t("admin.cmdb.catalog.subtitle")}</p>
        </div>
        {active && selected !== null && (
          <Button variant="primary" onClick={() => setEditing("new")} data-testid="cmdb-catalog-new">
            {t("admin.cmdb.catalog.new")}
          </Button>
        )}
      </div>

      {statusQ.data && <CmdbModeNotice status={statusQ.data} />}

      {active && (
        <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
          <nav aria-label={t("admin.cmdb.catalog.classes")} className="rounded-xl border border-hairline bg-surface p-1" data-testid="cmdb-catalog-classes">
            {classesQ.isLoading && <div className="flex justify-center p-4"><Spinner /></div>}
            {classes.map((c) => (
              <button
                key={c}
                type="button"
                aria-current={selected === c || undefined}
                data-testid={`cmdb-catalog-class-${c}`}
                onClick={() => setSelected(c)}
                className={`block w-full truncate rounded px-2 py-1.5 text-left text-sm ${selected === c ? "bg-accent-dim font-medium text-ink" : "text-muted hover:bg-surface-subtle hover:text-ink"}`}
              >
                {c}
              </button>
            ))}
          </nav>

          <div className="overflow-x-auto rounded-xl border border-hairline bg-surface" data-testid="cmdb-catalog-items">
            {itemsQ.isLoading ? (
              <div className="flex justify-center p-6"><Spinner /></div>
            ) : items.length === 0 ? (
              <p className="p-4 text-sm text-muted">{t("admin.table.empty")}</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-hairline text-left text-xs uppercase tracking-wide text-muted">
                    <th className="px-3 py-2 font-medium">{t("admin.cmdb.catalog.name")}</th>
                    <th className="px-3 py-2 font-medium">{t("admin.cmdb.catalog.key")}</th>
                    {hasFunctionality && <th className="px-3 py-2 font-medium">{t("admin.cmdb.catalog.functionality")}</th>}
                    <th className="px-3 py-2 font-medium">{t("admin.cmdb.catalog.position")}</th>
                    <th className="px-3 py-2 font-medium">{t("admin.table.status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => (
                    <tr key={it.id} className="border-b border-hairline last:border-0" data-testid={`cmdb-catalog-row-${it.item_key}`}>
                      <td className="px-3 py-2">
                        <button type="button" className="font-medium text-accent hover:underline" onClick={() => setEditing(it)} data-testid={`cmdb-catalog-edit-${it.item_key}`}>
                          {it.name}
                        </button>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-muted">{it.item_key}</td>
                      {hasFunctionality && (
                        <td className="px-3 py-2 text-ink">
                          {it.functionality ? t(`admin.cmdb.catalog.functionalities.${it.functionality}`, { defaultValue: it.functionality }) : "—"}
                        </td>
                      )}
                      <td className="px-3 py-2 text-ink">{it.position}</td>
                      <td className="px-3 py-2">
                        <Badge tone={it.valid ? "success" : "muted"}>{it.valid ? t("admin.table.valid") : t("admin.table.invalid")}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {editing !== null && selected !== null && (
        <ItemDialog
          catalogClass={selected}
          item={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void qc.invalidateQueries({ queryKey: ["admin", "cmdb", "catalog"] });
            void qc.invalidateQueries({ queryKey: ["admin", "cmdb", "status"] });
          }}
        />
      )}
    </div>
  );
}
