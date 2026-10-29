import { useTranslation } from "react-i18next";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { refHref, refLabel } from "./changeUi";

/** A reference the caller may read: a link where an agent page exists, else its name. */
export function RefLink({ refKey }: { refKey: string }) {
  const { t } = useTranslation();
  const href = refHref(refKey);
  const label = refLabel(refKey, t);
  return href ? (
    <RouterAnchor href={href} className="text-accent hover:underline">
      {label}
    </RouterAnchor>
  ) : (
    <span className="text-ink">{label}</span>
  );
}

/** "n weitere ohne Leserecht": what the API left out because the caller cannot read it. */
export function HiddenCount({ count, testId }: { count: number; testId: string }) {
  const { t } = useTranslation();
  if (count <= 0) return null;
  return (
    <span className="text-xs text-muted" data-testid={testId}>
      {t("itsm.change.hiddenCount", { count })}
    </span>
  );
}
