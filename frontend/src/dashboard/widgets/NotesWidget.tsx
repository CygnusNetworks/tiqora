import { useTranslation } from "react-i18next";
import type { WidgetProps } from "@/plugins/dashboardWidgets";
import { MarkdownView } from "@/components/kb/MarkdownView";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetEmpty } from "../chrome/WidgetDataBody";

export interface NotesSettings {
  markdown: string;
}

/** Free text (markdown, sanitised by `MarkdownView`); shared with the dashboard it is on. */
export function NotesWidget({ settings, mode }: WidgetProps<NotesSettings>) {
  const { t } = useTranslation();
  const markdown = typeof settings.markdown === "string" ? settings.markdown.trim() : "";
  return (
    <WidgetFrame title={t("dashboards.widgets.notes.title")} mode={mode} testId="notes-widget">
      {markdown ? (
        <MarkdownView markdown={markdown} className="leading-relaxed" data-testid="notes-markdown" />
      ) : (
        <WidgetEmpty>{t("dashboards.widgets.notes.empty")}</WidgetEmpty>
      )}
    </WidgetFrame>
  );
}
