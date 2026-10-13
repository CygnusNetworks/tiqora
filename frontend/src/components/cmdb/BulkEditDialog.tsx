import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { LookupCombobox } from "@/components/fields/LookupCombobox";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { apiErrorMessage } from "@/lib/avatarApi";
import { cmdbApi, type CatalogItem, type CmdbBulkIn } from "@/lib/cmdbApi";

type TargetKind = "ci" | "service" | "ticket";
const INPUT_CLS =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent";
const KEEP = 0;

/**
 * "Mehrere bearbeiten": one deployment state, one incident state and/or one link for all selected
 * CIs. Only the chosen fields are sent; the job reports per CI what was changed or skipped.
 */
export function BulkEditDialog({
  open,
  ciIds,
  deploymentItems,
  incidentItems,
  onClose,
  onStarted,
}: {
  open: boolean;
  ciIds: number[];
  deploymentItems: CatalogItem[];
  incidentItems: CatalogItem[];
  onClose: () => void;
  onStarted: (jobId: number) => void;
}) {
  const { t } = useTranslation();
  const [deployment, setDeployment] = useState(KEEP);
  const [incident, setIncident] = useState(KEEP);
  const [withLink, setWithLink] = useState(false);
  const [linkType, setLinkType] = useState<string | null>(null);
  const [targetKind, setTargetKind] = useState<TargetKind>("ci");
  const [targetKey, setTargetKey] = useState("");
  const [targetLabel, setTargetLabel] = useState("");
  const [ciSide, setCiSide] = useState<"source" | "target">("source");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const linkTypesQ = useQuery({ queryKey: ["cmdb", "link-types"], queryFn: () => cmdbApi.linkTypes(), enabled: open && withLink });
  const offered = (linkTypesQ.data ?? []).filter((lt) =>
    targetKind === "ci" ? lt.offer_ci_ci : targetKind === "service" ? lt.offer_ci_service : lt.offer_ticket_ci,
  );
  const selectedType = offered.find((lt) => lt.name === linkType) ?? null;

  const linkReady = withLink && selectedType !== null && targetKey.trim() !== "";
  const hasChange = deployment !== KEEP || incident !== KEEP || linkReady;

  const submit = async () => {
    const body: CmdbBulkIn = { ci_ids: ciIds };
    if (deployment !== KEEP) body.deployment_state_id = deployment;
    if (incident !== KEEP) body.incident_state_id = incident;
    if (linkReady && selectedType) {
      body.link = { link_type_key: selectedType.name, target_kind: targetKind, target_key: targetKey.trim(), ci_side: ciSide };
    }
    setBusy(true);
    setError(null);
    try {
      const accepted = await cmdbApi.startBulk(body);
      onStarted(accepted.job_id);
    } catch (err) {
      setError(apiErrorMessage(err, t("cmdb.list.bulk.failed")));
    } finally {
      setBusy(false);
    }
  };

  const stateItems = (items: CatalogItem[]) => [
    { value: KEEP, label: t("cmdb.list.bulk.keep") },
    ...items.map((i) => ({ value: i.id, label: i.name })),
  ];

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("cmdb.list.bulk.title")}
      description={t("cmdb.list.bulk.description", { count: ciIds.length })}
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} data-testid="bulk-cancel">
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={() => void submit()} disabled={!hasChange || busy} data-testid="bulk-submit">
            {t("cmdb.list.bulk.start")}
          </Button>
        </>
      }
    >
      <div className="space-y-4" data-testid="bulk-edit-dialog">
        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink">{t("cmdb.list.deploymentState")}</span>
          <SelectField<number> testId="bulk-deployment" value={deployment} items={stateItems(deploymentItems)} onChange={setDeployment} aria-label={t("cmdb.list.deploymentState")} />
        </label>
        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink">{t("cmdb.list.incidentState")}</span>
          <SelectField<number> testId="bulk-incident" value={incident} items={stateItems(incidentItems)} onChange={setIncident} aria-label={t("cmdb.list.incidentState")} />
        </label>

        <div className="space-y-3 border-t border-hairline pt-3">
          <div className="flex items-center gap-2">
            <Switch checked={withLink} onChange={setWithLink} testId="bulk-link-switch" aria-label={t("cmdb.list.bulk.addLink")} />
            <span className="text-sm text-ink">{t("cmdb.list.bulk.addLink")}</span>
          </div>
          {withLink && (
            <div className="space-y-3" data-testid="bulk-link-fields">
              <SegmentedControl<TargetKind>
                testId="bulk-target-kind"
                aria-label={t("cmdb.list.bulk.targetKind")}
                value={targetKind}
                items={[
                  { value: "ci", label: t("cmdb.list.bulk.kinds.ci") },
                  { value: "service", label: t("cmdb.list.bulk.kinds.service") },
                  { value: "ticket", label: t("cmdb.list.bulk.kinds.ticket") },
                ]}
                onChange={(k) => {
                  setTargetKind(k);
                  setTargetKey("");
                  setTargetLabel("");
                  setLinkType(null);
                }}
              />
              <SelectField<string>
                testId="bulk-link-type"
                aria-label={t("cmdb.list.bulk.linkType")}
                placeholder={t("cmdb.form.select")}
                value={linkType}
                items={offered.map((lt) => ({ value: lt.name, label: lt.name }))}
                onChange={setLinkType}
              />
              {selectedType?.pointed && (
                <SegmentedControl<"source" | "target">
                  testId="bulk-ci-side"
                  aria-label={t("cmdb.list.bulk.ciSide")}
                  value={ciSide}
                  items={[
                    { value: "source", label: selectedType.source_label ?? t("cmdb.list.bulk.side.source") },
                    { value: "target", label: selectedType.target_label ?? t("cmdb.list.bulk.side.target") },
                  ]}
                  onChange={setCiSide}
                />
              )}
              {targetKind === "ci" ? (
                <LookupCombobox
                  id="bulk-target"
                  testId="bulk-target"
                  ariaLabel={t("cmdb.list.bulk.target")}
                  hasValue={targetKey !== ""}
                  shownLabel={targetLabel}
                  queryKey={["cmdb", "bulk-ci-lookup"]}
                  search={async (term) => {
                    const rows = await cmdbApi.lookup(term);
                    return rows.map((r) => ({ value: String(r.id), label: `${r.number} – ${r.name}`, hint: r.class_name }));
                  }}
                  onPick={(hit) => {
                    setTargetKey(hit.value);
                    setTargetLabel(hit.label);
                  }}
                  onClear={() => {
                    setTargetKey("");
                    setTargetLabel("");
                  }}
                  placeholder={t("cmdb.form.ciSearch")}
                />
              ) : (
                <input
                  className={INPUT_CLS}
                  value={targetKey}
                  onChange={(e) => setTargetKey(e.target.value)}
                  aria-label={t("cmdb.list.bulk.target")}
                  placeholder={t(`cmdb.list.bulk.keyHint.${targetKind}`)}
                  data-testid="bulk-target"
                />
              )}
            </div>
          )}
        </div>
        {error && (
          <p role="alert" className="text-sm text-danger" data-testid="bulk-error">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
