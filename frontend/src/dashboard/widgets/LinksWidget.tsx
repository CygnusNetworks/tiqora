import type { MouseEvent } from "react";
import { useRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { linkTabIndex, rendersLinks } from "../chrome/mode";
import { useIsKiosk } from "../wallboard/kioskContext";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetEmpty } from "../chrome/WidgetDataBody";
import { isSafeLinkUrl, linkKind } from "./linkUrl";

export interface LinksSettings {
  items: { label: string; url: string }[];
}

const ITEM_CLASS =
  "flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm text-ink transition-colors hover:bg-surface-subtle hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";

/** Important addresses. Only `/path`, `http(s)` and `mailto` urls render (as on the server). */
export function LinksWidget({ settings, mode }: WidgetProps<LinksSettings>) {
  const { t } = useTranslation();
  const router = useRouter();
  const kiosk = useIsKiosk();
  const items = (Array.isArray(settings.items) ? settings.items : []).filter(
    (i): i is { label: string; url: string } =>
      !!i && typeof i.label === "string" && i.label.trim() !== "" && isSafeLinkUrl(i.url),
  );
  const internalClick = (url: string) => (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    router.history.push(url);
  };
  return (
    <WidgetFrame title={t("dashboards.widgets.links.title")} mode={mode} testId="links-widget">
      {items.length === 0 ? (
        <WidgetEmpty>{t("dashboards.widgets.links.empty")}</WidgetEmpty>
      ) : (
        <ul className="-mx-2 flex flex-col gap-0.5">
          {items.map((item, i) => {
            const kind = linkKind(item.url);
            return (
              <li key={`${i}-${item.url}`}>
                {!rendersLinks(mode, kiosk) ? (
                  <span className={ITEM_CLASS}>{item.label}</span>
                ) : (
                  <a
                    href={item.url}
                    tabIndex={linkTabIndex(mode)}
                    className={ITEM_CLASS}
                    {...(kind === "external"
                      ? { target: "_blank", rel: "noopener noreferrer" }
                      : kind === "internal"
                        ? { onClick: internalClick(item.url) }
                        : {})}
                  >
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    {kind === "external" && (
                      <span aria-hidden="true" className="shrink-0 text-xs text-muted">
                        ↗
                      </span>
                    )}
                  </a>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </WidgetFrame>
  );
}
