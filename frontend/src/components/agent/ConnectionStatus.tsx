import { useTranslation } from "react-i18next";
import { useConnectionStatus } from "@/lib/useSSE";
import { DotIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";

/**
 * Compact live/reconnecting indicator for the realtime event stream, driven
 * by `useConnectionStatus()`. A single dot — green when the stream is open,
 * amber while (re)connecting — with the state named in its tooltip and to
 * screen readers. Sits in the top bar next to the notification bell.
 */
export function ConnectionStatus() {
  const { t } = useTranslation();
  const state = useConnectionStatus();
  const live = state === "live";
  const label = live ? t("connection.live") : t("connection.reconnecting");

  return (
    <span
      data-testid="connection-status"
      data-state={state}
      title={label}
      className="flex h-8 w-6 items-center justify-center"
    >
      <DotIcon
        className={cn("text-[9px]", live ? "text-green" : "animate-pulse text-amber")}
      />
      <span className="sr-only">{label}</span>
    </span>
  );
}
