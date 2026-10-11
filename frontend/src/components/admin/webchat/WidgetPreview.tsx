import { useCallback, useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { Values } from "@/lib/webchatForm";
import { PROTOCOL, toPreviewConfig, type PreviewConfig } from "@/lib/webchatPreview";

const DEBOUNCE_MS = 200;
const SIZES = { desktop: { width: 380, height: 600 }, mobile: { width: 360, height: 640 } } as const;

/**
 * The chat frame in preview mode (`preview=1`, own origin): it renders what the
 * editor holds right now. The draft goes in via `postMessage` to the frame's exact
 * origin (never `*`), on load and 200 ms after every change.
 */
export function WidgetPreview({
  publicKey,
  values,
  device,
}: {
  publicKey: string;
  values: Values;
  device: "desktop" | "mobile";
}) {
  const { t } = useTranslation();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const lastValid = useRef<PreviewConfig | null>(null);
  const src = useMemo(
    () =>
      `/chat/v1/frame?k=${encodeURIComponent(publicKey)}&origin=${encodeURIComponent(window.location.origin)}&preview=1`,
    [publicKey],
  );
  const config = useMemo(() => toPreviewConfig(values), [values]);

  const post = useCallback(() => {
    const target = frameRef.current?.contentWindow;
    if (!target || !lastValid.current) return;
    target.postMessage(
      { p: PROTOCOL, type: "preview-config", config: lastValid.current },
      new URL(src, window.location.href).origin,
    );
  }, [src]);

  // The frame attaches its listener after it has loaded, so the first draft goes out when it
  // says `ready` (again after every reload, e.g. a renewed key); only its own window counts.
  useEffect(() => {
    const frameOrigin = new URL(src, window.location.href).origin;
    const onMessage = (e: MessageEvent) => {
      const d = e.data as { p?: unknown; type?: unknown } | null;
      if (
        e.source !== frameRef.current?.contentWindow ||
        e.origin !== frameOrigin ||
        !d ||
        d.p !== PROTOCOL ||
        d.type !== "ready"
      ) {
        return;
      }
      post();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [src, post]);

  useEffect(() => {
    if (config) lastValid.current = config;
    const timer = window.setTimeout(post, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [config, post]);

  const size = SIZES[device];
  return (
    <div className="overflow-auto rounded-lg border border-hairline bg-surface-subtle p-3">
      <iframe
        ref={frameRef}
        src={src}
        title={t("webchat.admin.look.previewTitle")}
        data-testid="webchat-widget-preview"
        style={{ width: size.width, height: size.height, maxWidth: "100%" }}
        className="mx-auto block rounded-lg border border-hairline bg-white shadow"
      />
    </div>
  );
}
