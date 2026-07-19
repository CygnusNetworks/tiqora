import { cn } from "@/lib/cn";
import type { CategoryOut } from "@/lib/api";
import { useTranslation } from "react-i18next";
import { useMemo } from "react";

export type CategoryNode = CategoryOut & { children: CategoryNode[] };

/** Build a parent_id-based tree from the flat CategoryOut[] the API returns. */
export function buildCategoryTree(categories: CategoryOut[]): CategoryNode[] {
  const byId = new Map<number, CategoryNode>();
  for (const c of categories) byId.set(c.id, { ...c, children: [] });

  const roots: CategoryNode[] = [];
  for (const c of categories) {
    const node = byId.get(c.id)!;
    const parent = c.parent_id != null ? byId.get(c.parent_id) : undefined;
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const bySort = (a: CategoryNode, b: CategoryNode) =>
    a.sort - b.sort || a.name.localeCompare(b.name);
  const sortRec = (nodes: CategoryNode[]) => {
    nodes.sort(bySort);
    for (const n of nodes) sortRec(n.children);
  };
  sortRec(roots);
  return roots;
}

/** Flatten tree back to a list, depth-first (for tests / lookups). */
export function flattenCategories(nodes: CategoryNode[]): CategoryNode[] {
  const out: CategoryNode[] = [];
  const walk = (list: CategoryNode[]) => {
    for (const n of list) {
      out.push(n);
      if (n.children.length) walk(n.children);
    }
  };
  walk(nodes);
  return out;
}

/** Walk the parent_id chain to build breadcrumbs, root-first. */
export function categoryBreadcrumbs(
  categories: CategoryOut[],
  categoryId: number | null | undefined,
): CategoryOut[] {
  const byId = new Map(categories.map((c) => [c.id, c]));
  const chain: CategoryOut[] = [];
  let current = categoryId != null ? byId.get(categoryId) : undefined;
  const seen = new Set<number>();
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    chain.unshift(current);
    current = current.parent_id != null ? byId.get(current.parent_id) : undefined;
  }
  return chain;
}

export type CategoryTreeProps = {
  categories: CategoryOut[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  counts?: Record<number, number>;
  className?: string;
};

function CategoryItem({
  node,
  depth,
  selectedId,
  onSelect,
  counts,
}: {
  node: CategoryNode;
  depth: number;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  counts?: Record<number, number>;
}) {
  const active = selectedId === node.id;
  const count = counts?.[node.id] ?? 0;

  return (
    <li>
      <button
        type="button"
        data-testid={`category-node-${node.id}`}
        onClick={() => onSelect(node.id)}
        className={cn(
          "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
          active
            ? "bg-surface-subtle font-medium text-accent"
            : "text-ink hover:bg-surface-subtle",
          !node.valid && "opacity-50",
        )}
        style={{ paddingLeft: 8 + depth * 12 }}
      >
        <span className="min-w-0 flex-1 truncate" title={node.name}>
          {node.name}
        </span>
        {counts && (
          <span className="shrink-0 font-mono text-xs tabular-nums text-muted">
            {count}
          </span>
        )}
      </button>
      {node.children.length > 0 && (
        <ul className="list-none">
          {node.children.map((child) => (
            <CategoryItem
              key={child.id}
              node={child}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              counts={counts}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

/** Sidebar tree for KB categories — same visual pattern as QueueTree. */
export function CategoryTree({
  categories,
  selectedId,
  onSelect,
  counts,
  className,
}: CategoryTreeProps) {
  const { t } = useTranslation();
  const tree = useMemo(() => buildCategoryTree(categories), [categories]);

  return (
    <nav
      className={cn("flex flex-col gap-1", className)}
      data-testid="category-tree"
      aria-label={t("kb.sidebar")}
    >
      <button
        type="button"
        data-testid="category-node-all"
        onClick={() => onSelect(null)}
        className={cn(
          "flex w-full items-center rounded px-2 py-1.5 text-left text-sm transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
          selectedId == null
            ? "bg-surface-subtle font-medium text-accent"
            : "text-ink hover:bg-surface-subtle",
        )}
      >
        {t("kb.allCategories")}
      </button>
      <ul className="list-none space-y-0.5">
        {tree.map((node) => (
          <CategoryItem
            key={node.id}
            node={node}
            depth={0}
            selectedId={selectedId}
            onSelect={onSelect}
            counts={counts}
          />
        ))}
      </ul>
      {tree.length === 0 && (
        <p className="px-2 py-4 text-xs text-muted">{t("kb.noCategories")}</p>
      )}
    </nav>
  );
}
