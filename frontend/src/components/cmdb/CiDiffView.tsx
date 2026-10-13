import { useTranslation } from "react-i18next";
import type { CiDiff } from "@/lib/cmdbApi";

const KIND_KEY = { added: "added", removed: "removed", changed: "changed" } as const;

/**
 * Changed attribute values between two versions. Every row carries a `+` / `−` text marker and a
 * written kind ("hinzugefügt", "entfernt", "geändert") so the change reads without colour.
 */
export function CiDiffView({ diff }: { diff: CiDiff }) {
  const { t } = useTranslation();
  if (diff.rows.length === 0) {
    return (
      <p className="text-sm text-muted" data-testid="ci-diff-empty">
        {t("cmdb.detail.diff.empty")}
      </p>
    );
  }
  const empty = <span className="text-muted">{t("cmdb.form.empty")}</span>;
  return (
    <table className="w-full text-sm" data-testid="ci-diff">
      <thead className="text-xs text-muted">
        <tr>
          <th scope="col" className="px-2 py-1 text-left font-medium">
            {t("cmdb.detail.diff.attribute")}
          </th>
          <th scope="col" className="px-2 py-1 text-left font-medium">
            {t("cmdb.detail.diff.change")}
          </th>
        </tr>
      </thead>
      <tbody>
        {diff.rows.map((r) => (
          <tr key={`${r.kind}:${r.path}`} data-testid={`ci-diff-row-${r.path}`} data-kind={r.kind} className="border-t border-hairline align-top">
            <td className="px-2 py-1.5">
              <div className="font-medium text-ink">{r.label}</div>
              <div className="text-xs text-muted">{t(`cmdb.detail.diff.${KIND_KEY[r.kind]}`)}</div>
            </td>
            <td className="space-y-0.5 px-2 py-1.5 font-mono text-xs">
              {r.kind !== "added" && (
                <div className="flex gap-2 text-danger">
                  <span aria-hidden="true" className="w-3 shrink-0">
                    −
                  </span>
                  <span className="break-all">{r.old ?? empty}</span>
                </div>
              )}
              {r.kind !== "removed" && (
                <div className="flex gap-2 text-green">
                  <span aria-hidden="true" className="w-3 shrink-0">
                    +
                  </span>
                  <span className="break-all">{r.new ?? empty}</span>
                </div>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
