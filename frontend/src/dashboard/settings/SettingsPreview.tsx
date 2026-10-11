import { Component, useEffect, useMemo, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { WidgetDefinition } from "@/plugins/dashboardWidgets";
import { WidgetChromeProvider } from "../chrome/WidgetFrame";
import { WidgetFrame } from "../chrome/WidgetFrame";
import { WidgetDataProvider, usePreviewData } from "../data";
import { widgetTexts } from "../gallery/galleryModel";

/** Debounce of the preview request: one request after typing pauses, none per keystroke. */
export const PREVIEW_DEBOUNCE_MS = 400;
const NO_FILTERS = Object.freeze({});

class PreviewBoundary extends Component<{ children: ReactNode; fallback: ReactNode; resetKey: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidUpdate(prev: { resetKey: string }) {
    if (this.state.failed && prev.resetKey !== this.props.resetKey) this.setState({ failed: false });
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/**
 * The real widget rendered in `preview` mode with the draft settings. Data comes from the preview
 * endpoint (`usePreviewData`, debounced, equal settings share one request); only the last valid
 * draft is sent. The box is inert (no links, no tab stops) and has a fixed maximum height.
 */
export function SettingsPreview({
  def,
  settings,
  valid,
  titleOverride,
  size,
}: {
  def: WidgetDefinition;
  settings: Record<string, unknown>;
  valid: boolean;
  titleOverride: string | null;
  size: { w: number; h: number };
}) {
  const { t } = useTranslation();
  const lastValid = useRef(settings);
  if (valid) lastValid.current = settings;
  const shown = lastValid.current;
  const { refetch, ...state } = usePreviewData(def.key, shown, { debounceMs: PREVIEW_DEBOUNCE_MS });
  const dataValue = useMemo(() => ({ ...state, refetch }), [state.status, state.data, state.error, refetch]); // eslint-disable-line react-hooks/exhaustive-deps
  const boxRef = useRef<HTMLDivElement>(null);
  // `inert` as an attribute: nothing in the preview can take focus or be clicked.
  useEffect(() => {
    boxRef.current?.setAttribute("inert", "");
  }, []);
  const Widget = def.component;
  const title = titleOverride?.trim() || widgetTexts(def, t).title;
  const content = <Widget instanceId="preview" settings={shown} size={size} filters={NO_FILTERS} mode="preview" />;
  return (
    <section aria-label={t("dashboards.settings.preview")} className="rounded-lg border border-hairline bg-surface-subtle p-3">
      <h3 className="mb-2 text-xs font-medium text-muted">{t("dashboards.settings.preview")}</h3>
      <div ref={boxRef} data-testid="settings-preview" className="max-h-56 overflow-hidden">
        <PreviewBoundary
          resetKey={JSON.stringify(shown)}
          fallback={<p className="text-sm text-danger">{t("dashboards.view.widgetCrashed")}</p>}
        >
          <WidgetChromeProvider value={{ titleOverride, canEdit: false }}>
            <WidgetDataProvider value={dataValue}>
              <div data-testid="widget-component" data-mode="preview">
                {def.owner === "core" ? (
                  content
                ) : (
                  <WidgetFrame title={title} mode="preview" testId="settings-preview-frame">
                    {content}
                  </WidgetFrame>
                )}
              </div>
            </WidgetDataProvider>
          </WidgetChromeProvider>
        </PreviewBoundary>
      </div>
    </section>
  );
}
