import { useTranslation } from "react-i18next";
import type { FieldOption } from "@/components/fields/types";
import { SelectMenu } from "@/components/ui/SelectMenu";
import type { GroupGrant } from "@/lib/identityApi";
import { GROUP_PERMISSIONS } from "./useDirectory";

/** Group rights to hand out: one row per group, one checkbox per Znuny permission. */
export function GroupGrantsEditor({
  grants,
  onChange,
  groups,
  disabled,
  testId,
}: {
  grants: GroupGrant[];
  onChange: (next: GroupGrant[]) => void;
  groups: FieldOption[];
  disabled?: boolean;
  testId: string;
}) {
  const { t } = useTranslation();
  const nameOf = (id: number) => groups.find((g) => g.value === String(id))?.label ?? `#${id}`;
  const rest = groups.filter((g) => !grants.some((x) => String(x.group_id) === g.value));
  const update = (index: number, grant: GroupGrant) => onChange(grants.map((g, i) => (i === index ? grant : g)));

  return (
    <div className="space-y-2" data-testid={testId}>
      {grants.map((grant, i) => {
        const perms = grant.permissions ?? [];
        return (
          <div
            key={grant.group_id}
            data-testid={`${testId}-${i}`}
            className="rounded border border-hairline bg-surface-subtle px-2.5 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-ink">{nameOf(grant.group_id)}</span>
              {!disabled && (
                <button
                  type="button"
                  data-testid={`${testId}-${i}-remove`}
                  aria-label={t("admin.identity.roles.removeGroup", { name: nameOf(grant.group_id) })}
                  onClick={() => onChange(grants.filter((_, idx) => idx !== i))}
                  className="text-xs text-muted hover:text-danger"
                >
                  ✕
                </button>
              )}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              {GROUP_PERMISSIONS.map((perm) => (
                <label key={perm} className="flex items-center gap-1.5 text-[12.5px] text-ink">
                  <input
                    type="checkbox"
                    data-testid={`${testId}-${i}-perm-${perm}`}
                    checked={perms.includes(perm)}
                    disabled={disabled}
                    onChange={(e) =>
                      update(i, {
                        ...grant,
                        permissions: e.target.checked ? [...perms, perm] : perms.filter((p) => p !== perm),
                      })
                    }
                  />
                  {t(`admin.identity.roles.perm.${perm}`)}
                </label>
              ))}
            </div>
            {perms.includes("rw") && (
              <p data-testid={`${testId}-${i}-rw-hint`} className="mt-1 text-xs text-muted">
                {t("admin.identity.roles.rwHint")}
              </p>
            )}
          </div>
        );
      })}
      {!disabled && rest.length > 0 && (
        <SelectMenu
          items={rest}
          onSelect={(v: string) => onChange([...grants, { group_id: Number(v), permissions: ["rw"] }])}
          panelTestId={`${testId}-add-menu`}
          trigger={({ ref, toggleProps }) => (
            <button
              ref={ref}
              type="button"
              data-testid={`${testId}-add`}
              {...toggleProps}
              className="rounded-full border border-dashed border-hairline px-2.5 py-0.5 text-[12px] text-muted hover:text-ink focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            >
              + {t("admin.identity.roles.addGroup")}
            </button>
          )}
        />
      )}
    </div>
  );
}
