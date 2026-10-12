import { BaseEdge, EdgeLabelRenderer, getBezierPath, type Edge, type EdgeProps } from "@xyflow/react";
import { cn } from "@/lib/cn";
import type { CanvasEdge } from "./canvasModel";

export type TransitionFlowEdge = Edge<CanvasEdge["data"], "transition">;

/** A transition: bezier line with the transition name as label. */
export function TransitionEdge(props: EdgeProps<TransitionFlowEdge>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected, markerEnd } = props;
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke: data?.issues ? "var(--color-danger)" : selected ? "var(--color-accent)" : "var(--color-muted)",
          strokeWidth: selected ? 2.5 : 1.5,
        }}
      />
      <EdgeLabelRenderer>
        <div
          data-testid="process-designer-edge-label"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          className={cn(
            "pointer-events-auto absolute rounded border bg-surface px-1.5 py-0.5 text-xs text-ink",
            selected ? "border-accent" : "border-hairline",
          )}
        >
          {data?.name}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
