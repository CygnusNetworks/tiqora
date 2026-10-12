import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { apiErrorMessage } from "@/lib/avatarApi";
import { cmdbApi, cmdbSavedSearchesKey, useCmdbSavedSearches, type CiSearchCriteria, type CmdbSavedSearch } from "@/lib/cmdbApi";


/** Dropdown of saved searches plus "Suche speichern" (optionally shared) and delete for own ones. */
export function CiSavedSearches({
  selectedId,
  criteria,
  onSelect,
}: {
  selectedId: number | null;
  /** What gets stored when the current search is saved. */
  criteria: CiSearchCriteria;
  onSelect: (search: CmdbSavedSearch | null) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const listQ = useCmdbSavedSearches();
  const list = listQ.data ?? [];
  const selected = list.find((s) => s.id === selectedId) ?? null;
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => cmdbApi.createSavedSearch({ name: name.trim(), shared, criteria }),
    onSuccess: (created) => {
      void qc.invalidateQueries({ queryKey: cmdbSavedSearchesKey });
      setSaving(false);
      setName("");
      setShared(false);
      onSelect(created);
    },
    onError: (err) => setError(apiErrorMessage(err, t("cmdb.list.saved.failed"))),
  });
  const remove = useMutation({
    mutationFn: (id: number) => cmdbApi.removeSavedSearch(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: cmdbSavedSearchesKey });
      onSelect(null);
    },
  });

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="ci-saved-searches">
      <div className="w-56">
        <SelectField<number>
          testId="ci-saved-select"
          aria-label={t("cmdb.list.saved.label")}
          value={selected?.id ?? 0}
          items={[
            { value: 0, label: t("cmdb.list.saved.none") },
            ...list.map((s) => ({ value: s.id, label: s.shared && !s.owned ? `${s.name} (${t("cmdb.list.saved.sharedTag")})` : s.name })),
          ]}
          onChange={(id) => onSelect(id === 0 ? null : (list.find((s) => s.id === id) ?? null))}
        />
      </div>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => {
          setError(null);
          setSaving(true);
        }}
        data-testid="ci-saved-save"
      >
        {t("cmdb.list.saved.save")}
      </Button>
      {selected?.owned && (
        <Button size="sm" variant="ghost" onClick={() => remove.mutate(selected.id)} disabled={remove.isPending} data-testid="ci-saved-delete">
          {t("cmdb.list.saved.delete")}
        </Button>
      )}
      <Dialog
        open={saving}
        onClose={() => setSaving(false)}
        title={t("cmdb.list.saved.save")}
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setSaving(false)}>
              {t("common.cancel")}
            </Button>
            <Button variant="primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()} data-testid="ci-saved-confirm">
              {t("cmdb.list.saved.save")}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="block space-y-1">
            <span className="text-xs font-medium text-ink">{t("cmdb.list.saved.name")}</span>
            <input
              className="w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent"
              value={name}
              maxLength={100}
              onChange={(e) => setName(e.target.value)}
              data-testid="ci-saved-name"
            />
          </label>
          <div className="flex items-center gap-2">
            <Switch checked={shared} onChange={setShared} testId="ci-saved-shared" aria-label={t("cmdb.list.saved.shared")} />
            <span className="text-sm text-ink">{t("cmdb.list.saved.shared")}</span>
          </div>
          {error && (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          )}
        </div>
      </Dialog>
    </div>
  );
}
