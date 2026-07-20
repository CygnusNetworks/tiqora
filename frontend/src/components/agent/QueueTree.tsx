import { cn } from "@/lib/cn";
import type { QueueNode } from "@/lib/api";
import { useTranslation } from "react-i18next";

export type QueueTreeProps = {
  queues: QueueNode[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  className?: string;
};

function QueueItem({
  node,
  depth,
  selectedId,
  onSelect,
}: {
  node: QueueNode;
  depth: number;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}) {
  const { t } = useTranslation();
  const active = selectedId === node.id;
  const open = node.counts?.open ?? 0;
  const total = node.counts?.total ?? 0;
  const newCount = node.counts?.new ?? 0;

  return (
    <li>
      <button
        type="button"
        data-testid={`queue-node-${node.id}`}
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
          {node.name.includes("::")
            ? node.name.split("::").pop()
            : node.name}
        </span>
        {newCount > 0 && (
          <span
            className="shrink-0 rounded-full bg-accent-dim px-1.5 py-0.5 font-mono text-[10px] font-semibold tabular-nums text-accent"
            title={t("queue.newCount", { count: newCount })}
          >
            {t("queue.newCount", { count: newCount })}
          </span>
        )}
        <span className="shrink-0 font-mono text-xs tabular-nums text-muted">
          {open}
          {total !== open ? (
            <span className="text-muted/60">/{total}</span>
          ) : null}
        </span>
      </button>
      {node.children && node.children.length > 0 && (
        <ul className="list-none">
          {node.children.map((child) => (
            <QueueItem
              key={child.id}
              node={child}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export function QueueTree({
  queues,
  selectedId,
  onSelect,
  className,
}: QueueTreeProps) {
  const { t } = useTranslation();

  return (
    <nav
      className={cn("flex flex-col gap-1", className)}
      data-testid="queue-tree"
      aria-label={t("queue.sidebar")}
    >
      <button
        type="button"
        data-testid="queue-node-all"
        onClick={() => onSelect(null)}
        className={cn(
          "flex w-full items-center rounded px-2 py-1.5 text-left text-sm transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
          selectedId == null
            ? "bg-surface-subtle font-medium text-accent"
            : "text-ink hover:bg-surface-subtle",
        )}
      >
        {t("queue.allQueues")}
      </button>
      <ul className="list-none space-y-0.5">
        {queues.map((q) => (
          <QueueItem
            key={q.id}
            node={q}
            depth={0}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        ))}
      </ul>
      {queues.length === 0 && (
        <p className="px-2 py-4 text-xs text-muted">{t("queue.empty")}</p>
      )}
    </nav>
  );
}

/** Flatten tree for tests / dashboard shortcuts. */
export function flattenQueues(nodes: QueueNode[]): QueueNode[] {
  const out: QueueNode[] = [];
  const walk = (list: QueueNode[]) => {
    for (const n of list) {
      out.push(n);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return out;
}
