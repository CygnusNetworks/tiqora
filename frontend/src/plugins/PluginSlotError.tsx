/** What a plugin slot shows instead of the plugin when it failed (PluginBoundary). */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { FrontendPlugin } from "@/lib/pluginsApi";

export function PluginSlotError({ plugin, error }: { plugin: FrontendPlugin; error: Error }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div role="alert" className="rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-muted">
      <div className="flex items-center justify-between gap-2">
        <span>{t("plugins.slotError", { name: plugin.name })}</span>
        <button
          type="button"
          className="shrink-0 text-xs text-muted underline hover:text-ink"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {t("plugins.slotErrorDetails")}
        </button>
      </div>
      {open ? <pre className="mt-2 whitespace-pre-wrap break-words text-xs text-danger">{error.message}</pre> : null}
    </div>
  );
}
