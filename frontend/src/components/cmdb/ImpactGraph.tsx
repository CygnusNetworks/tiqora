import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { usePhoneWidth } from "@/components/process-designer/usePhoneWidth";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { cmdbApi, type ImpactGraph as ImpactGraphData } from "@/lib/cmdbApi";
import { CiStateBadge } from "./CiStateBadge";
import { impactTree, layoutImpact, nodeLabel, type ImpactTreeItem } from "./impactLayout";

type GraphNode = ImpactGraphData["nodes"][number];
type ImpactFlowNode = Node<{ node: GraphNode; root: boolean }, "impact">;

export type Direction = "both" | "down" | "up";
const DEPTHS = [1, 2, 3, 4, 5] as const;

/** One chip: label, incident state badge; a CI the viewer may not read is grey and says so. */
function ImpactNode({ data }: NodeProps<ImpactFlowNode>) {
  const { t } = useTranslation();
  const { node, root } = data;
  return (
    <div
      data-testid={`impact-node-${node.id}`}
      className={cn(
        "w-56 rounded-lg border px-3 py-2 text-sm shadow-sm",
        node.hidden ? "border-hairline bg-surface-subtle text-muted" : "bg-surface text-ink",
        root ? "border-accent ring-2 ring-accent" : !node.hidden && "border-hairline",
        node.kind === "ci" && !node.hidden && "cursor-pointer",
      )}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      {node.hidden ? (
        <span>{t("cmdb.hiddenNode")}</span>
      ) : (
        <div className="space-y-1">
          <div className="truncate font-medium" title={nodeLabel(node)}>
            {nodeLabel(node)}
          </div>
          <div className="flex items-center gap-1.5 text-xs text-muted">
            {node.kind === "service" && <span>{t("cmdb.impact.service")}</span>}
            {node.incident_functionality && (
              <CiStateBadge kind="incident" functionality={node.incident_functionality} label={t(`cmdb.state.${node.incident_functionality}`, { defaultValue: node.incident_functionality })} />
            )}
          </div>
        </div>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}

const nodeTypes = { impact: ImpactNode };

const themeVars = {
  "--xy-background-color": "var(--color-surface)",
  "--xy-controls-button-background-color": "var(--color-surface)",
  "--xy-controls-button-background-color-hover": "var(--color-surface-subtle)",
  "--xy-controls-button-color": "var(--color-ink)",
  "--xy-controls-button-border-color": "var(--color-hairline)",
  "--xy-edge-stroke": "var(--color-muted)",
  "--xy-attribution-background-color": "transparent",
} as React.CSSProperties;

function toFlow(graph: ImpactGraphData): { nodes: ImpactFlowNode[]; edges: Edge[] } {
  const layout = layoutImpact(graph);
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const nodes: ImpactFlowNode[] = layout.nodes.map((p) => ({
    id: p.id,
    type: "impact",
    position: { x: p.x, y: p.y },
    data: { node: byId.get(p.id) as GraphNode, root: p.id === graph.root },
    draggable: false,
    connectable: false,
    focusable: false,
  }));
  const edges: Edge[] = layout.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.link_type,
    focusable: false,
    markerEnd: { type: MarkerType.ArrowClosed },
    // propagating links solid, the others dashed
    style: { strokeWidth: e.propagates ? 2 : 1.25, strokeDasharray: e.propagates ? undefined : "6 4", stroke: e.propagates ? "var(--color-ink)" : "var(--color-muted)" },
    labelStyle: { fontSize: 11, fill: "var(--color-muted)" },
    labelBgStyle: { fill: "var(--color-surface)" },
  }));
  return { nodes, edges };
}

function TreeList({ item, depth }: { item: ImpactTreeItem; depth: number }) {
  const { t } = useTranslation();
  const { node, via } = item;
  const label = node.hidden ? t("cmdb.hiddenNode") : nodeLabel(node);
  return (
    <li data-testid={`impact-item-${node.id}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1 text-sm">
        {via && (
          <span className="text-xs text-muted">
            {t(via.outgoing ? "cmdb.impact.out" : "cmdb.impact.in", { type: via.link_type })}
            {via.propagates ? ` · ${t("cmdb.impact.propagates")}` : ""}
          </span>
        )}
        {node.kind === "ci" && !node.hidden && node.ci_id != null && depth > 0 ? (
          <Link to="/agent/cmdb/$ciId" params={{ ciId: String(node.ci_id) }} className="font-medium text-accent hover:underline">
            {label}
          </Link>
        ) : (
          <span className={cn(node.hidden ? "text-muted" : "font-medium text-ink")}>{label}</span>
        )}
        {!node.hidden && node.kind === "service" && <span className="text-xs text-muted">{t("cmdb.impact.service")}</span>}
        {!node.hidden && node.incident_functionality && (
          <CiStateBadge kind="incident" functionality={node.incident_functionality} label={t(`cmdb.state.${node.incident_functionality}`, { defaultValue: node.incident_functionality })} />
        )}
      </div>
      {item.children.length > 0 && (
        <ul className="ml-4 border-l border-hairline pl-3">
          {item.children.map((c) => (
            <TreeList key={c.node.id} item={c} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

function Canvas({ graph }: { graph: ImpactGraphData }) {
  const navigate = useNavigate();
  const flow = useMemo(() => toFlow(graph), [graph]);
  return (
    <div className="h-[28rem] rounded-md border border-hairline" style={themeVars} data-testid="impact-canvas" aria-hidden="true">
      <ReactFlow
        nodes={flow.nodes}
        edges={flow.edges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        deleteKeyCode={null}
        fitView
        minZoom={0.2}
        onNodeClick={(_, n) => {
          const d = (n as ImpactFlowNode).data.node;
          if (d.kind === "ci" && !d.hidden && d.ci_id != null && n.id !== graph.root) void navigate({ to: "/agent/cmdb/$ciId", params: { ciId: String(d.ci_id) } });
        }}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

/**
 * Dependency graph of a CI: a read-only canvas on wide screens (pan and zoom, click a CI to open it),
 * a nested text list below 768 px and always in the DOM for screen readers. Depth 1 to 5 and the
 * reading direction are server-side. Lazy: this chunk carries the graph library.
 */
export function ImpactGraph({ ciId }: { ciId: number }) {
  const { t } = useTranslation();
  const phone = usePhoneWidth();
  const [depth, setDepth] = useState<number>(2);
  const [direction, setDirection] = useState<Direction>("both");
  const q = useQuery({
    queryKey: ["cmdb", "ci", ciId, "impact", depth, direction],
    queryFn: () => cmdbApi.impact(ciId, { depth, direction }),
    placeholderData: keepPreviousData,
  });
  const tree = useMemo(() => (q.data ? impactTree(q.data) : null), [q.data]);
  const alone = q.data ? q.data.nodes.length <= 1 : false;

  return (
    <section aria-labelledby="ci-impact-title" className="space-y-3" data-testid="ci-impact">
      <h3 id="ci-impact-title" className="text-sm font-semibold text-ink">
        {t("cmdb.impact.title")}
      </h3>
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <div id="ci-impact-depth-label" className="mb-1 text-xs font-medium text-muted">
            {t("cmdb.impact.depth")}
          </div>
          <SegmentedControl
            testId="impact-depth"
            aria-labelledby="ci-impact-depth-label"
            items={DEPTHS.map((d) => ({ value: d, label: String(d) }))}
            value={depth}
            onChange={setDepth}
          />
        </div>
        <div>
          <div id="ci-impact-direction-label" className="mb-1 text-xs font-medium text-muted">
            {t("cmdb.impact.direction")}
          </div>
          <SegmentedControl
            testId="impact-direction"
            aria-labelledby="ci-impact-direction-label"
            items={[
              { value: "both", label: t("cmdb.impact.directions.both") },
              { value: "down", label: t("cmdb.impact.directions.down") },
              { value: "up", label: t("cmdb.impact.directions.up") },
            ]}
            value={direction}
            onChange={setDirection}
          />
        </div>
      </div>
      {q.isError && (
        <p role="alert" className="text-sm text-danger">
          {t("cmdb.impact.loadFailed")}
        </p>
      )}
      {q.isPending && <Spinner />}
      {q.data && (
        <>
          {q.data.truncated && (
            <p role="status" className="rounded-md border border-escalation/30 bg-escalation/15 px-3 py-1.5 text-sm text-ink" data-testid="impact-truncated">
              {t("cmdb.impact.truncated")}
            </p>
          )}
          {alone ? (
            <p className="text-sm text-muted" data-testid="impact-empty">
              {t("cmdb.impact.empty")}
            </p>
          ) : (
            <>
              {!phone && (
                <ReactFlowProvider>
                  <Canvas graph={q.data} />
                </ReactFlowProvider>
              )}
              <div className={cn(!phone && "sr-only")} data-testid="impact-list">
                {tree && (
                  <ul aria-label={t("cmdb.impact.listLabel")}>
                    <TreeList item={tree} depth={0} />
                  </ul>
                )}
              </div>
            </>
          )}
        </>
      )}
      {!alone && q.data && !phone && (
        <p className="text-xs text-muted">{t("cmdb.impact.legend")}</p>
      )}
    </section>
  );
}

export default ImpactGraph;
