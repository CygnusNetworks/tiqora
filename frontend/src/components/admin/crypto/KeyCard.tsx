import type { ComponentType, ReactNode, SVGProps } from "react";
import { Menu } from "@/components/ui/Menu";
import { MoreIcon } from "@/components/ui/icons";

/** One key / certificate as a card: icon, title, facts, state chips, actions. */
export function KeyCard({
  icon: Icon,
  title,
  meta,
  chips,
  actions,
  menu,
  menuLabel,
  testId,
}: {
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  title: ReactNode;
  meta: ReactNode[];
  chips: ReactNode;
  actions?: ReactNode;
  menu?: ReactNode;
  menuLabel: string;
  testId: string;
}) {
  return (
    <article
      className="grid grid-cols-[2.75rem_minmax(0,1fr)] gap-x-3.5 gap-y-3 rounded-xl border border-hairline bg-surface p-4 sm:grid-cols-[2.75rem_minmax(0,1fr)_auto]"
      data-testid={testId}
    >
      <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent-dim text-accent">
        <Icon className="h-5 w-5" />
      </span>
      <div className="min-w-0 space-y-1">
        <div className="break-words text-sm font-semibold text-ink">{title}</div>
        <div className="flex flex-wrap gap-x-3.5 gap-y-1 text-xs text-muted">
          {meta.map((m, i) => (
            <span key={i} className="min-w-0 break-all">
              {m}
            </span>
          ))}
        </div>
        <div className="flex flex-wrap gap-1.5 pt-1">{chips}</div>
      </div>
      <div className="col-span-2 flex flex-wrap items-start justify-end gap-1.5 sm:col-span-1">
        {actions}
        {menu ? (
          <Menu
            trigger={({ ref, toggleProps }) => (
              <button
                ref={ref}
                type="button"
                aria-label={menuLabel}
                data-testid={`${testId}-menu`}
                className="rounded-md p-1.5 text-muted hover:bg-surface-subtle hover:text-ink"
                {...toggleProps}
              >
                <MoreIcon className="h-4 w-4" />
              </button>
            )}
          >
            {menu}
          </Menu>
        ) : null}
      </div>
    </article>
  );
}
