import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { cmdbApi, type RenderedAttr } from "@/lib/cmdbApi";

/** Only http(s) targets become links (F14): imported and Znuny free text must never carry `javascript:`. */
function safeHref(raw: string): string | null {
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

function Value({ node }: { node: RenderedAttr }) {
  const { t } = useTranslation();
  const text = node.display ?? "";
  const ref = node.ref;
  if (ref?.kind === "ci" && /^\d+$/.test(ref.key)) {
    return (
      <Link to="/agent/cmdb/$ciId" params={{ ciId: ref.key }} className="text-accent hover:underline">
        {text}
      </Link>
    );
  }
  if (ref?.kind === "attachment" && /^\d+$/.test(ref.key)) {
    return (
      <a href={cmdbApi.attachmentUrl(Number(ref.key))} download className="text-accent hover:underline">
        {text}
      </a>
    );
  }
  if (ref?.kind === "url" || (node.type === "url" && !ref)) {
    const href = safeHref(ref?.key ?? text);
    if (href) {
      return (
        <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
          {text}
        </a>
      );
    }
  }
  if (node.display === null) return <span className="text-muted">{t("cmdb.form.empty")}</span>;
  return <span>{text}</span>;
}

function Nodes({ nodes, depth }: { nodes: RenderedAttr[]; depth: number }) {
  return (
    <dl className={depth > 0 ? "mt-1 space-y-1 border-l border-hairline pl-4" : "space-y-2"}>
      {nodes.map((n) => (
        <div key={n.path} data-testid={`ci-view-${n.path}`}>
          <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-[minmax(8rem,14rem)_1fr]">
            <dt className="text-xs font-medium text-muted">{n.label}</dt>
            <dd className="min-w-0 break-words text-sm text-ink">{n.display !== null || n.children.length === 0 ? <Value node={n} /> : null}</dd>
          </div>
          {n.children.length > 0 && <Nodes nodes={n.children} depth={depth + 1} />}
        </div>
      ))}
    </dl>
  );
}

/** Read-only attribute tree of a CI version (`RenderedAttr` from the API). */
export function CiAttributeView({ rendered }: { rendered: RenderedAttr[] }) {
  const { t } = useTranslation();
  if (rendered.length === 0) {
    return (
      <p className="text-sm text-muted" data-testid="ci-view-empty">
        {t("cmdb.form.noAttributes")}
      </p>
    );
  }
  return (
    <div data-testid="ci-view">
      <Nodes nodes={rendered} depth={0} />
    </div>
  );
}
