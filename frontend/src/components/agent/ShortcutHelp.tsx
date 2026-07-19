import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";

const SHORTCUTS = [
  { keys: "j / k", actionKey: "shortcuts.rowNav" },
  { keys: "Enter", actionKey: "shortcuts.openTicket" },
  { keys: "?", actionKey: "shortcuts.thisHelp" },
  { keys: "/", actionKey: "shortcuts.focusSearch" },
] as const;

export function ShortcutHelp({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onClose={onClose} title={t("shortcuts.title")}>
      <ul className="space-y-2">
        {SHORTCUTS.map((s) => (
          <li key={s.keys} className="flex items-center justify-between gap-4">
            <kbd className="rounded border border-hairline bg-surface-subtle px-2 py-0.5 font-mono text-xs">
              {s.keys}
            </kbd>
            <span className="text-muted">{t(s.actionKey)}</span>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}
