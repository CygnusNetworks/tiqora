/**
 * Renders a sanitised article body.
 * HTML → fully sandboxed iframe (allow-scripts only, never allow-same-origin)
 *         with auto-height via postMessage / polling.
 * Plain text → pre-styled block (API returns HTML-escaped text).
 * External images (data-external-src) stay blocked until the user opts in.
 */
import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

const HEIGHT_MSG = "tiqora-article-height";

function buildIframeSrcDoc(
  html: string,
  loadExternal: boolean,
  frameId: string,
): string {
  const activateExternal = loadExternal
    ? `document.querySelectorAll('img[data-external-src]').forEach(function(img){
         var s=img.getAttribute('data-external-src');
         if(s){ img.setAttribute('src', s); img.removeAttribute('data-external-src'); }
       });`
    : "";

  // Escape frameId for embedding in JS string
  const safeId = frameId.replace(/\\/g, "\\\\").replace(/'/g, "\\'");

  return `<!DOCTYPE html><html><head><meta charset="utf-8"/>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob: http: https:; style-src 'unsafe-inline'; font-src data:;"/>
<style>
  html,body{margin:0;padding:0;background:transparent;color:#0f172a;font:14px/1.5 system-ui,sans-serif;word-wrap:break-word;overflow-wrap:anywhere;}
  img{max-width:100%;height:auto;}
  a{color:#2563eb;}
  pre,code{font-family:ui-monospace,monospace;font-size:12px;}
  table{border-collapse:collapse;max-width:100%;}
  td,th{border:1px solid #e2e8f0;padding:4px 6px;}
  img[data-external-src]{outline:1px dashed #d97706;min-width:24px;min-height:24px;background:#fef3c7;}
</style></head><body>${html}
<script>
(function(){
  ${activateExternal}
  function report(){
    var h=Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 1);
    parent.postMessage({type:'${HEIGHT_MSG}', height:h, id:'${safeId}'}, '*');
  }
  if(document.readyState==='complete') report();
  else window.addEventListener('load', report);
  try { new ResizeObserver(report).observe(document.body); } catch(e) {}
  setInterval(report, 500);
})();
</script></body></html>`;
}

export type ArticleBodyRendererProps = {
  body: string;
  isHtml: boolean;
  className?: string;
};

export function ArticleBodyRenderer({
  body,
  isHtml,
  className,
}: ArticleBodyRendererProps) {
  const { t } = useTranslation();
  const frameId = useId();
  const [height, setHeight] = useState(80);
  const [loadExternal, setLoadExternal] = useState(false);

  const hasExternal = isHtml && /data-external-src\s*=/i.test(body);

  useEffect(() => {
    const onMessage = (ev: MessageEvent) => {
      const data = ev.data as { type?: string; height?: number; id?: string };
      if (!data || data.type !== HEIGHT_MSG) return;
      if (data.id !== frameId) return;
      if (typeof data.height === "number" && data.height > 0) {
        setHeight(Math.min(Math.max(data.height + 4, 40), 4000));
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [frameId]);

  useEffect(() => {
    setLoadExternal(false);
  }, [body]);

  const srcDoc = useMemo(
    () => (isHtml ? buildIframeSrcDoc(body, loadExternal, frameId) : ""),
    [isHtml, body, loadExternal, frameId],
  );

  const onLoadExternal = useCallback(() => setLoadExternal(true), []);

  if (!isHtml) {
    return (
      <pre
        className={cn(
          "max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded border border-hairline bg-surface-subtle p-3 font-mono text-xs text-ink",
          className,
        )}
        data-testid="article-body-plain"
        // API HTML-escapes plain text; render as HTML so entities decode safely.
        dangerouslySetInnerHTML={{ __html: body }}
      />
    );
  }

  return (
    <div className={cn("space-y-2", className)} data-testid="article-body-html">
      {hasExternal && !loadExternal && (
        <div
          className="flex flex-wrap items-center justify-between gap-2 rounded border border-escalation/40 bg-escalation/10 px-3 py-2 text-xs text-escalation"
          data-testid="external-images-banner"
        >
          <span>{t("ticket.externalImagesBlocked")}</span>
          <Button size="sm" variant="secondary" onClick={onLoadExternal}>
            {t("ticket.loadExternalImages")}
          </Button>
        </div>
      )}
      <iframe
        title={t("ticket.articleBody")}
        srcDoc={srcDoc}
        sandbox="allow-scripts"
        className="w-full rounded border border-hairline bg-white"
        style={{ height, minHeight: 40 }}
        data-testid="article-body-iframe"
      />
    </div>
  );
}
