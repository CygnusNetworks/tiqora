import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { SelectField } from "@/components/ui/SelectField";
import { FIXED_TARGETS, normalizeTarget, type MappingRow, type TargetOption } from "./mappingModel";

/**
 * One row per file column: sample values and the target it fills. A target can be picked once;
 * an empty choice means the column is not imported.
 */
export function MappingEditor({
  rows,
  samples,
  options,
  onChange,
}: {
  rows: MappingRow[];
  /** Per column, the first non-empty cells (CSV only; null = the server supplies them in the dry run). */
  samples: string[][] | null;
  options: TargetOption[];
  onChange: (rows: MappingRow[]) => void;
}) {
  const { t } = useTranslation();

  const items = useMemo(
    () => [
      ...FIXED_TARGETS.map((f) => ({ value: f as string, label: t(`cmdb.import.fixed.${f}`) })),
      ...options,
    ],
    [options, t],
  );
  const used = useMemo(() => new Set(rows.filter((r) => r.target !== "").map((r) => normalizeTarget(r.target))), [rows]);

  return (
    <div className="overflow-x-auto rounded-md border border-hairline" data-testid="mapping-editor">
      <table className="w-full text-sm">
        <thead className="bg-surface-subtle text-left text-xs text-muted">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">
              {t("cmdb.import.map.column")}
            </th>
            <th scope="col" className="hidden px-3 py-2 font-medium sm:table-cell">
              {t("cmdb.import.map.samples")}
            </th>
            <th scope="col" className="px-3 py-2 font-medium sm:min-w-56">
              {t("cmdb.import.map.target")}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const sample = samples?.[i] ?? [];
            const own = row.target === "" ? "" : normalizeTarget(row.target);
            return (
              <tr key={`${i}-${row.column}`} className="border-t border-hairline align-top" data-testid={`mapping-row-${i}`}>
                <td className="min-w-0 break-words px-3 py-2 font-medium text-ink">
                  {row.column || <span className="text-muted">{t("cmdb.import.map.unnamed", { n: i + 1 })}</span>}
                  {/* phones: the samples sit under the column name */}
                  {sample.length > 0 && <span className="mt-0.5 line-clamp-1 block break-words text-xs font-normal text-muted sm:hidden">{sample.join(" · ")}</span>}
                </td>
                <td className="hidden max-w-64 px-3 py-2 text-xs text-muted sm:table-cell" data-testid={`mapping-samples-${i}`}>
                  {samples === null ? "–" : sample.length === 0 ? "" : <span className="line-clamp-2 break-words">{sample.join(" · ")}</span>}
                </td>
                <td className="px-3 py-2">
                  <SelectField<string>
                    testId={`mapping-target-${i}`}
                    aria-label={t("cmdb.import.map.targetFor", { column: row.column })}
                    value={row.target}
                    placeholder={t("cmdb.import.map.skip")}
                    items={[
                      { value: "", label: t("cmdb.import.map.skip") },
                      ...items.map((it) => {
                        const key = normalizeTarget(it.value);
                        const taken = used.has(key) && key !== own;
                        return { value: it.value, label: it.label, disabled: taken, hint: taken ? t("cmdb.import.map.taken") : undefined };
                      }),
                    ]}
                    onChange={(target) => onChange(rows.map((r, j) => (j === i ? { ...r, target } : r)))}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
