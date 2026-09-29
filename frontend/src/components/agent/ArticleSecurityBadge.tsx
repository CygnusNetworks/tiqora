import { useTranslation } from "react-i18next";
import type { ArticleSecurity } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Popover } from "@/components/ui/Popover";
import { cn } from "@/lib/cn";
import { securityLook } from "./useArticleSecurity";

function SecurityDetails({ security }: { security: ArticleSecurity }) {
  const { t } = useTranslation();
  const rows: [string, string][] = [
    [t("ticket.security.method"), security.method === "smime" ? "S/MIME" : "PGP"],
    [
      t("ticket.security.layers"),
      [
        security.signed ? t("ticket.security.signed") : null,
        security.encrypted ? t("ticket.security.encrypted") : null,
      ]
        .filter(Boolean)
        .join(" + ") || "–",
    ],
  ];
  if (security.signer) rows.push([t("ticket.security.signer"), security.signer]);
  if (security.key_id) rows.push([t("ticket.security.keyId"), security.key_id]);
  if (security.detail) rows.push([t("ticket.security.detail"), security.detail]);
  return (
    <div className="space-y-2 text-xs">
      <p className="font-medium text-ink">
        {t(`ticket.security.status.${security.status}`, {
          defaultValue: security.status,
        })}
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="break-all text-ink">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Clickable badge in an article header; the popover shows method, signer,
 * key id and detail. `compact` renders only the glyph (conversation bubbles). */
export function ArticleSecurityBadge({
  articleId,
  security,
  compact = false,
}: {
  articleId: number;
  security: ArticleSecurity;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const { tone, icon } = securityLook(security);
  const label = t(`ticket.security.short.${security.status}`, {
    defaultValue: security.status,
  });
  return (
    <Popover
      align="left"
      label={t("ticket.security.title")}
      panelTestId={`article-security-panel-${articleId}`}
      trigger={({ ref, toggleProps }) => (
        <button
          type="button"
          ref={ref}
          {...toggleProps}
          data-testid={`article-security-${articleId}`}
          data-status={security.status}
          title={t(`ticket.security.status.${security.status}`, {
            defaultValue: security.status,
          })}
          className="inline-flex"
        >
          {compact ? (
            <span
              aria-hidden
              className={cn(
                "text-[11px]",
                tone === "danger" && "text-danger",
                tone === "warn" && "text-escalation",
                tone === "success" && "text-green",
              )}
            >
              {icon}
            </span>
          ) : (
            <Badge tone={tone}>
              {icon} {label}
            </Badge>
          )}
        </button>
      )}
    >
      <SecurityDetails security={security} />
    </Popover>
  );
}

/** Non-interactive glyph for rows that are themselves buttons (timeline). */
export function ArticleSecurityMarker({
  articleId,
  security,
}: {
  articleId: number;
  security: ArticleSecurity;
}) {
  const { t } = useTranslation();
  const { tone, icon } = securityLook(security);
  const title = t(`ticket.security.status.${security.status}`, {
    defaultValue: security.status,
  });
  return (
    <Badge tone={tone} data-testid={`article-security-marker-${articleId}`} title={title}>
      {icon}
    </Badge>
  );
}
