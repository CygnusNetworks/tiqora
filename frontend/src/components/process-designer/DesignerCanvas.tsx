import { useMemo } from "react";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { ProcessDraft } from "@/lib/processDesignerApi";

/** Where an activity without a stored position goes (a simple grid, until `autoLayout` lands). */
function fallbackPosition(index: number): { x: number; y: number } {
  return { x: 40 + (index % 3) * 220, y: 40 + Math.floor(index / 3) * 120 };
}

/** Skeleton canvas: one node per activity and one edge per transition, read-only for now. */
export function DesignerCanvas({ draft }: { draft: ProcessDraft }) {
  const { nodes, edges } = useMemo(() => {
    const nodes: Node[] = draft.activities.map((a, i) => {
      const pos = draft.layout[a.entity_id];
      const x = pos ? Number(pos.left) : NaN;
      const y = pos ? Number(pos.top) : NaN;
      return {
        id: a.entity_id,
        position: Number.isFinite(x) && Number.isFinite(y) ? { x, y } : fallbackPosition(i),
        data: { label: a.name },
      };
    });
    const path = (draft.process.config.Path ?? {}) as Record<string, Record<string, { ActivityEntityID?: string }>>;
    const edges: Edge[] = [];
    for (const [from, transitions] of Object.entries(path)) {
      for (const [tid, def] of Object.entries(transitions ?? {})) {
        if (def?.ActivityEntityID) {
          edges.push({ id: `${from}:${tid}`, source: from, target: def.ActivityEntityID });
        }
      }
    }
    return { nodes, edges };
  }, [draft]);

  return (
    <div className="h-full min-h-[420px] w-full" data-testid="process-designer-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        deleteKeyCode={null}
        fitView
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
