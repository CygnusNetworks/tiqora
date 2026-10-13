import { useTranslation } from "react-i18next";
import { ciRefHref, safeHref } from "@/lib/ciDisplay";
import type { CiAttribute } from "@/lib/itsmApi";
import { RouterAnchor } from "@/components/ui/RouterAnchor";

function Value({ attr, onNavigate }: { attr: CiAttribute; onNavigate?: (href: string) => void }) {
  const text = attr.display || attr.raw || "";
  const ciHref = attr.kind === "ci" ? ciRefHref(attr.ref) : null;
  if (ciHref) {
    return (
      <RouterAnchor
        href={ciHref}
        className="text-accent hover:underline"
        data-testid="ci-attr-ci-link"
        onClick={(e) => {
          if (!onNavigate || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
          e.preventDefault();
          onNavigate(ciHref);
        }}
      >
        {text}
      </RouterAnchor>
    );
  }
  if (attr.kind === "url" || attr.kind === "email") {
    const raw = attr.raw ?? attr.display;
    const href = safeHref(attr.kind === "email" && raw && !raw.includes(":") ? `mailto:${raw}` : raw);
    if (href) {
      return (
        <a href={href} className="text-accent hover:underline" rel="noopener noreferrer" target="_blank" data-testid="ci-attr-link">
          {text}
        </a>
      );
    }
  }
  return <span className="whitespace-pre-wrap break-words">{text}</span>;
}

function Nodes({ attrs, depth, onNavigate }: { attrs: readonly CiAttribute[]; depth: number; onNavigate?: (href: string) => void }) {
  return (
    <ul className={depth === 0 ? "space-y-1.5" : "mt-1 space-y-1 border-l border-hairline pl-3"} role={depth === 0 ? "tree" : "group"}>
      {attrs.map((attr, i) => (
        <li key={`${attr.key}-${i}`} role="treeitem" aria-expanded={attr.children?.length ? true : undefined} data-testid="ci-attr">
          <div className="flex flex-wrap gap-x-2 text-sm">
            <span className="text-muted">{attr.label}</span>
            {(attr.display || attr.raw) && <Value attr={attr} onNavigate={onNavigate} />}
          </div>
          {attr.children && attr.children.length > 0 && <Nodes attrs={attr.children} depth={depth + 1} onNavigate={onNavigate} />}
        </li>
      ))}
    </ul>
  );
}

/**
 * The attributes of a configuration item: nested sub-attributes and repeated entries as a tree,
 * CI references as links (`ciDetailHref`), URLs only as links when http(s)/mailto.
 * `onNavigate` lets a page route the CI links through the router instead of a page load.
 */
export function CiAttributeTree({ attributes, onNavigate }: { attributes: readonly CiAttribute[]; onNavigate?: (href: string) => void }) {
  const { t } = useTranslation();
  if (attributes.length === 0) {
    return <p className="text-sm text-muted">{t("itsm.ci.noAttributes")}</p>;
  }
  return <Nodes attrs={attributes} depth={0} onNavigate={onNavigate} />;
}
