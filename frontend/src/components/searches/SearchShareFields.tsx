import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { MultiSelectInput } from "@/components/fields/MultiSelectInput";
import { reportsApi } from "@/lib/reportsApi";

/**
 * Who besides the owner sees a saved search: the members of the chosen groups. A search has no
 * "everyone" option (unlike a report), so this is the group picker alone; an empty choice means
 * private. Group choices are the ones `ShareFields` offers (`GET /reference/share-groups`).
 */
export function SearchShareFields({
  value,
  onChange,
  canShare,
  testId = "search-share",
}: {
  value: number[];
  onChange: (next: number[]) => void;
  canShare: boolean;
  testId?: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  const groupsQ = useQuery({
    queryKey: ["reports", "share-groups"],
    queryFn: ({ signal }) => reportsApi.shareGroups(signal),
    enabled: canShare,
    staleTime: 60_000,
  });
  const options = (groupsQ.data ?? []).map((g) => ({ value: String(g.id), label: g.name }));
  return (
    <div className="space-y-1">
      <span id={`${id}-label`} className="block text-xs font-medium text-muted">
        {t("savedSearches.share")}
      </span>
      <MultiSelectInput
        value={value.map(String)}
        onChange={(next) => onChange(next.map(Number).filter(Number.isInteger))}
        options={options}
        labelId={`${id}-label`}
        describedBy={`${id}-hint`}
        testId={`${testId}-group-ids`}
        disabled={!canShare}
      />
      <p id={`${id}-hint`} className="text-xs text-muted" data-testid={`${testId}-hint`}>
        {canShare ? t("savedSearches.shareHint") : t("reports.share.needShareRight")}
      </p>
      {canShare && groupsQ.isSuccess && options.length === 0 && (
        <p className="text-xs text-muted">{t("reports.share.noGroups")}</p>
      )}
    </div>
  );
}
