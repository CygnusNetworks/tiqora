import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { cn } from "@/lib/cn";
import type { CanvasNode } from "./canvasModel";
import { StartNode } from "./StartNode";

export type ActivityFlowNode = Node<CanvasNode["data"], "activity">;

/** One process step: name, its dialogs, start/end markers and an issue count. */
export function ActivityNode({ data, selected }: NodeProps<ActivityFlowNode>) {
  return (
    <div
      data-testid="process-designer-node"
      className={cn(
        "w-52 rounded-lg border bg-surface px-3 py-2 text-sm shadow-sm",
        selected ? "border-accent ring-2 ring-accent" : "border-hairline",
        data.broken && "border-danger",
      )}
    >
      <Handle type="target" position={Position.Left} />
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium text-ink">{data.name}</span>
        {data.issues > 0 ? (
          <span className="rounded-full bg-danger px-1.5 text-[10px] text-white" data-testid="process-designer-node-issues">
            {data.issues}
          </span>
        ) : null}
      </div>
      {data.dialogs.length > 0 ? (
        <ul className="mt-1 space-y-0.5 text-xs text-muted">
          {data.dialogs.map((d) => (
            <li key={d.id} className="truncate">
              {d.name}
            </li>
          ))}
        </ul>
      ) : null}
      {data.isStart || data.isEnd ? (
        <div className="mt-1 flex gap-1">
          {data.isStart ? <StartNode kind="start" /> : null}
          {data.isEnd ? <StartNode kind="end" /> : null}
        </div>
      ) : null}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
