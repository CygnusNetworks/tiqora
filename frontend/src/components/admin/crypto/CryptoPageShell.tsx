import type { ComponentType, ReactNode, SVGProps } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";

export type CryptoTab = "overview" | "keys" | "settings";

type IconCmp = ComponentType<SVGProps<SVGSVGElement>>;

/**
 * Frame of the PGP / S-MIME admin pages: icon + title with the backend
 * state, then the three tabs (overview, keys, settings). The page owns the
 * tab state so overview actions ("fix", "next steps") can switch tabs.
 */
export function CryptoPageShell({
  backend,
  title,
  lede,
  icon: Icon,
  enabled,
  ready,
  tab,
  onTab,
  keysLabel,
  keyCount,
  settingsFlag,
  children,
}: {
  backend: "pgp" | "smime";
  title: string;
  lede: string;
  icon: IconCmp;
  enabled: boolean | null;
  ready: boolean | null;
  tab: CryptoTab;
  onTab: (tab: CryptoTab) => void;
  keysLabel: string;
  keyCount: number | null;
  settingsFlag: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const tabs: { id: CryptoTab; label: string; extra?: ReactNode }[] = [
    { id: "overview", label: t("admin.cryptoPage.tabOverview") },
    {
      id: "keys",
      label: keysLabel,
      extra:
        keyCount != null ? (
          <span className="rounded-full bg-surface-subtle px-1.5 text-[11px] font-semibold tabular-nums text-muted">
            {keyCount}
          </span>
        ) : null,
    },
    {
      id: "settings",
      label: t("admin.cryptoPage.tabSettings"),
      extra: settingsFlag ? (
        <span
          className="h-1.5 w-1.5 rounded-full bg-escalation"
          aria-label={t("admin.cryptoPage.settingsFlag")}
          data-testid={`crypto-settings-flag-${backend}`}
        />
      ) : null,
    },
  ];
  return (
    <div className="space-y-4 p-4" data-testid={`admin-${backend}-page`}>
      <div className="flex flex-wrap items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent-dim text-accent">
          <Icon className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="flex flex-wrap items-center gap-2 font-display text-xl font-semibold text-ink">
            {title}
            {enabled != null ? (
              <Badge tone={enabled ? "success" : "muted"} data-testid={`crypto-status-enabled-${backend}`}>
                {enabled ? t("admin.crypto.enabled") : t("admin.crypto.disabled")}
              </Badge>
            ) : null}
            {ready === false ? (
              <Badge tone="danger" data-testid={`crypto-status-unready-${backend}`}>
                {t("admin.cryptoPage.notReady")}
              </Badge>
            ) : null}
          </h1>
          <p className="mt-0.5 text-sm text-muted">{lede}</p>
        </div>
      </div>
      <div role="tablist" className="flex flex-wrap gap-1 border-b border-hairline">
        {tabs.map((item) => {
          const on = item.id === tab;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={on}
              data-testid={`crypto-tab-${item.id}`}
              onClick={() => onTab(item.id)}
              className={cn(
                "-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
                on ? "border-accent font-semibold text-ink" : "border-transparent text-muted hover:text-ink",
              )}
            >
              {item.label}
              {item.extra}
            </button>
          );
        })}
      </div>
      <div role="tabpanel">{children}</div>
    </div>
  );
}
