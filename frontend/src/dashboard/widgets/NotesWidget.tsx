import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { MarkdownView } from "@/components/kb/MarkdownView";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetEmpty } from "../chrome/WidgetDataBody";

export interface NotesSettings {
  markdown: string;
}

/** Free text (markdown, `MarkdownView` restricted profile: one person writes it for everyone the
 * dashboard is shared with, so no forms, images or embeds); shared with the dashboard it is on. */
export function NotesWidget({ settings, mode }: WidgetProps<NotesSettings>) {
  const { t } = useTranslation();
  const markdown = typeof settings.markdown === "string" ? settings.markdown.trim() : "";
  return (
    <WidgetFrame title={t("dashboards.widgets.notes.title")} mode={mode} testId="notes-widget">
      {markdown ? (
        <MarkdownView markdown={markdown} profile="restricted" className="leading-relaxed" data-testid="notes-markdown" />
      ) : (
        <WidgetEmpty>{t("dashboards.widgets.notes.empty")}</WidgetEmpty>
      )}
    </WidgetFrame>
  );
}
