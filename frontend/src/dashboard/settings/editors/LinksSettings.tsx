import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { MAX_LINKS, linkRows, type LinkRow } from "../validate";
import type { SettingsEditorProps } from "../registry";

const INPUT =
  "w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent";

/** Up to 12 label + address rows; the address is checked like the server does. */
export function LinksSettingsEditor({ value, onChange, errors }: SettingsEditorProps) {
  const { t } = useTranslation();
  const rows = linkRows(value);
  const set = (next: LinkRow[]) => onChange({ ...value, items: next });
  const patch = (i: number, p: Partial<LinkRow>) => set(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const move = (i: number, d: -1 | 1) => {
    const next = [...rows];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    set(next);
  };
  return (
    <fieldset className="min-w-0">
      <legend className="mb-2 text-xs font-medium text-muted">{t("dashboards.settings.links.legend")}</legend>
      <ul className="space-y-3">
        {rows.map((row, i) => {
          const n = i + 1;
          const labelErr = errors[`items.${i}.label`];
          const urlErr = errors[`items.${i}.url`];
          return (
            <li key={i} className="rounded-md border border-hairline p-2">
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="min-w-0">
                  <label htmlFor={`link-label-${i}`} className="mb-1 block text-xs text-muted">
                    {t("dashboards.settings.links.label", { n })}
                  </label>
                  <input
                    id={`link-label-${i}`}
                    value={row.label}
                    maxLength={80}
                    aria-invalid={labelErr ? true : undefined}
                    aria-describedby={labelErr ? `link-label-${i}-err` : undefined}
                    onChange={(e) => patch(i, { label: e.target.value })}
                    className={cn(INPUT, labelErr && "border-danger")}
                  />
                  {labelErr && (
                    <p id={`link-label-${i}-err`} role="alert" className="mt-1 text-xs text-danger">
                      {labelErr}
                    </p>
                  )}
                </div>
                <div className="min-w-0">
                  <label htmlFor={`link-url-${i}`} className="mb-1 block text-xs text-muted">
                    {t("dashboards.settings.links.url", { n })}
                  </label>
                  <input
                    id={`link-url-${i}`}
                    value={row.url}
                    inputMode="url"
                    aria-invalid={urlErr ? true : undefined}
                    aria-describedby={urlErr ? `link-url-${i}-err` : undefined}
                    onChange={(e) => patch(i, { url: e.target.value })}
                    className={cn(INPUT, urlErr && "border-danger")}
                  />
                  {urlErr && (
                    <p id={`link-url-${i}-err`} role="alert" className="mt-1 text-xs text-danger">
                      {urlErr}
                    </p>
                  )}
                </div>
              </div>
              <div className="mt-2 flex justify-end gap-1">
                <Button variant="ghost" size="sm" disabled={i === 0} onClick={() => move(i, -1)} aria-label={t("dashboards.settings.links.up", { n })}>
                  ↑
                </Button>
                <Button variant="ghost" size="sm" disabled={i === rows.length - 1} onClick={() => move(i, 1)} aria-label={t("dashboards.settings.links.down", { n })}>
                  ↓
                </Button>
                <Button variant="ghost" size="sm" onClick={() => set(rows.filter((_, j) => j !== i))} aria-label={t("dashboards.settings.links.remove", { n })}>
                  {t("dashboards.settings.links.removeShort")}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
      <div className="mt-3 flex items-center gap-3">
        <Button size="sm" disabled={rows.length >= MAX_LINKS} onClick={() => set([...rows, { label: "", url: "" }])}>
          {t("dashboards.settings.links.add")}
        </Button>
        <span className="text-xs text-muted">{t("dashboards.settings.links.count", { count: rows.length, max: MAX_LINKS })}</span>
      </div>
      <p className="mt-2 text-xs text-muted">{t("dashboards.settings.links.hint")}</p>
    </fieldset>
  );
}
