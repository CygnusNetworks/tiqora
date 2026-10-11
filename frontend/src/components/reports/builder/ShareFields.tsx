import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { MultiSelectInput } from "@/components/fields/MultiSelectInput";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { reportsApi, type SavedReportIn } from "@/lib/reportsApi";

export type ShareValue = { visibility: NonNullable<SavedReportIn["visibility"]>; group_ids: number[] };

/**
 * Who may see a saved report: only me, some groups, or everyone with report access.
 * Without the sharing right (`canShare` false) the choice is shown but locked, with the reason.
 * Group choices come from `GET /reference/share-groups` (the groups the person may share with).
 * Option test ids are `${testId}-private|groups|all`, the hint `${testId}-hint`, the group picker `${testId}-group-ids`.
 */
export function ShareFields({
  value,
  onChange,
  canShare,
  testId = "share-fields",
}: {
  value: ShareValue;
  onChange: (next: ShareValue) => void;
  canShare: boolean;
  testId?: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  const groupsQ = useQuery({
    queryKey: ["reports", "share-groups"],
    queryFn: ({ signal }) => reportsApi.shareGroups(signal),
    enabled: canShare && value.visibility === "groups",
    staleTime: 60_000,
  });
  const options = (groupsQ.data ?? []).map((g) => ({ value: String(g.id), label: g.name }));
  return (
    <div className="space-y-2">
      <span id={`${id}-label`} className="block text-xs font-medium text-muted">
        {t("reports.share.title")}
      </span>
      <SegmentedControl
        items={[
          { value: "private", label: t("reports.share.private") },
          { value: "groups", label: t("reports.share.groups") },
          { value: "all", label: t("reports.share.all") },
        ]}
        value={value.visibility}
        onChange={(visibility) => onChange({ visibility, group_ids: visibility === "groups" ? value.group_ids : [] })}
        disabled={!canShare}
        fullWidth
        testId={testId}
        aria-labelledby={`${id}-label`}
        aria-describedby={canShare ? undefined : `${id}-hint`}
      />
      {!canShare && (
        <p id={`${id}-hint`} className="text-xs text-muted" data-testid={`${testId}-hint`}>
          {t("reports.share.needShareRight")}
        </p>
      )}
      {value.visibility === "groups" && (
        <div className="space-y-1">
          <span id={`${id}-groups`} className="block text-xs text-muted">
            {t("reports.share.groupsLabel")}
          </span>
          <MultiSelectInput
            value={value.group_ids.map(String)}
            onChange={(next) => onChange({ ...value, group_ids: next.map(Number).filter(Number.isInteger) })}
            options={options}
            labelId={`${id}-groups`}
            testId={`${testId}-group-ids`}
            disabled={!canShare}
          />
          {canShare && groupsQ.isSuccess && options.length === 0 && (
            <p className="text-xs text-muted">{t("reports.share.noGroups")}</p>
          )}
        </div>
      )}
    </div>
  );
}
