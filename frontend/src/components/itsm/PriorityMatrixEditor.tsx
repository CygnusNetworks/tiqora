import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import type { PriorityMatrix } from "@/lib/itsmApi";

export type PriorityOption = { id: number; name: string };

const keyOf = (criticality: string, impact: string) => JSON.stringify([criticality, impact]);

/**
 * Criticality x impact grid with one priority select per cell. Read-only (plain names, no save)
 * when the matrix is Znuny's `cip_allocate` (Mode A): `matrix.editable` is false there.
 */
export function PriorityMatrixEditor({
  matrix,
  priorities,
  saving = false,
  error,
  onSave,
}: {
  matrix: PriorityMatrix;
  priorities: readonly PriorityOption[];
  saving?: boolean;
  error?: string | null;
  onSave: (cells: PriorityMatrix["cells"]) => void;
}) {
  const { t } = useTranslation();
  const initial = useMemo(() => {
    const map = new Map<string, number>();
    for (const c of matrix.cells) map.set(keyOf(c.criticality, c.impact), c.priority_id);
    return map;
  }, [matrix]);
  const [values, setValues] = useState<Map<string, number>>(initial);
  useEffect(() => setValues(initial), [initial]);

  const editable = matrix.editable;
  const nameOf = (id: number | undefined) => priorities.find((p) => p.id === id)?.name ?? (id ? `#${id}` : "–");
  const items = priorities.map((p) => ({ value: p.id, label: p.name }));
  const dirty = [...values].some(([k, v]) => initial.get(k) !== v) || values.size !== initial.size;

  const save = () => {
    const cells: PriorityMatrix["cells"] = [];
    for (const criticality of matrix.criticality_values) {
      for (const impact of matrix.impact_values) {
        const id = values.get(keyOf(criticality, impact));
        if (id) cells.push({ criticality, impact, priority_id: id });
      }
    }
    onSave(cells);
  };

  if (matrix.criticality_values.length === 0 || matrix.impact_values.length === 0) {
    return <p className="text-sm text-muted" data-testid="priority-matrix-empty">{t("admin.itsm.matrix.empty")}</p>;
  }

  return (
    <div className="space-y-3" data-testid="priority-matrix" data-editable={editable ? "true" : "false"}>
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse text-sm">
          <thead>
            <tr>
              <th className="p-2 text-left text-xs font-medium text-muted">
                {t("admin.itsm.matrix.criticality")} \ {t("admin.itsm.matrix.impact")}
              </th>
              {matrix.impact_values.map((impact) => (
                <th key={impact} scope="col" className="p-2 text-left text-xs font-medium text-muted">
                  {impact}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.criticality_values.map((criticality) => (
              <tr key={criticality} className="border-t border-hairline">
                <th scope="row" className="p-2 text-left text-xs font-medium text-ink">
                  {criticality}
                </th>
                {matrix.impact_values.map((impact) => {
                  const k = keyOf(criticality, impact);
                  const cellId = `matrix-cell-${matrix.criticality_values.indexOf(criticality)}-${matrix.impact_values.indexOf(impact)}`;
                  return (
                    <td key={impact} className="p-1.5">
                      {editable ? (
                        <SelectField
                          items={items}
                          value={values.get(k) ?? null}
                          placeholder="–"
                          testId={cellId}
                          aria-label={`${criticality} / ${impact}`}
                          onChange={(id) => setValues((prev) => new Map(prev).set(k, id))}
                        />
                      ) : (
                        <span data-testid={cellId}>{nameOf(initial.get(k))}</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {error && (
        <p role="alert" className="text-sm text-danger" data-testid="priority-matrix-error">
          {error}
        </p>
      )}
      {editable ? (
        <Button variant="primary" onClick={save} disabled={saving || !dirty} data-testid="priority-matrix-save">
          {t("admin.itsm.matrix.save")}
        </Button>
      ) : (
        <p className="text-sm text-muted" data-testid="priority-matrix-readonly">
          {t("admin.itsm.matrix.readOnly")}
        </p>
      )}
    </div>
  );
}
