import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Spinner } from "@/components/ui/Spinner";
import { ciStateColor } from "@/lib/ciDisplay";
import { itsmApi, itsmKeys, itsmErrorKey } from "@/lib/itsmApi";
import { stateColorVar } from "@/lib/status";
import { H, W, layoutGraph, nodeHref, type GraphNode } from "@/lib/linkGraphLayout";

const CI_COLORS = {
  green: "var(--color-green)",
  warn: "var(--color-escalation)",
  danger: "var(--color-danger)",
  muted: "var(--color-hairline)",
} as const;

function nodeColor(n: GraphNode): string {
  return n.kind === "ci" ? CI_COLORS[ciStateColor(n.incident_functionality)] : stateColorVar(n.state);
}

/** Read-only graph of a ticket's links (tickets with state colour, CIs with incident state). */
export function LinkGraph({ ticketId }: { ticketId: number }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: itsmKeys.linkGraph(ticketId),
    queryFn: ({ signal }) => itsmApi.linkGraph(ticketId, undefined, signal),
  });

  if (q.isPending) return <Spinner />;
  if (q.isError || !q.data) {
    return (
      <p className="text-xs text-danger" role="alert" data-testid="link-graph-error">
        {t(itsmErrorKey(q.error))}
      </p>
    );
  }
  const g = q.data;
  const { pos, width, height } = layoutGraph(g.nodes);
  const open = (n: GraphNode) => {
    const href = nodeHref(n);
    if (href) void navigate({ to: href });
  };

  return (
    <div className="space-y-1.5" data-testid="link-graph">
      <div className="overflow-auto rounded border border-hairline bg-surface-subtle/40 p-2">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="group"
          aria-label={t("itsm.graph.label")}
        >
          {g.edges.map((e, i) => {
            const a = pos.get(e.source);
            const b = pos.get(e.target);
            if (!a || !b) return null;
            const sameCol = a.x === b.x;
            const x1 = sameCol ? a.x + W / 2 : a.x < b.x ? a.x + W : a.x;
            const x2 = sameCol ? b.x + W / 2 : a.x < b.x ? b.x : b.x + W;
            const y1 = a.y + (sameCol ? (a.y < b.y ? H : 0) : H / 2);
            const y2 = b.y + (sameCol ? (a.y < b.y ? 0 : H) : H / 2);
            return (
              <g key={i} data-testid="link-graph-edge">
                <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--color-muted)" strokeWidth={1} />
                <text
                  x={(x1 + x2) / 2}
                  y={(y1 + y2) / 2 - 3}
                  textAnchor="middle"
                  fontSize={10}
                  fill="var(--color-muted)"
                >
                  {e.link_type}
                </text>
              </g>
            );
          })}
          {g.nodes.map((n) => {
            const p = pos.get(n.key);
            if (!p) return null;
            const href = nodeHref(n);
            return (
              <g
                key={n.key}
                transform={`translate(${p.x},${p.y})`}
                role={href ? "link" : undefined}
                tabIndex={href ? 0 : undefined}
                style={{ cursor: href ? "pointer" : "default" }}
                onClick={() => open(n)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    open(n);
                  }
                }}
                data-testid={`link-graph-node-${n.key}`}
                data-kind={n.kind}
              >
                <title>{`${n.number} ${n.title}`}</title>
                <rect
                  width={W}
                  height={H}
                  rx={6}
                  fill="var(--color-surface)"
                  stroke={n.key === g.root ? "var(--color-accent)" : "var(--color-hairline)"}
                  strokeWidth={n.key === g.root ? 2 : 1}
                />
                <rect width={5} height={H} rx={2} fill={nodeColor(n)} data-testid={`link-graph-color-${n.key}`} />
                <text x={14} y={19} fontSize={11} fontFamily="monospace" fill="var(--color-accent)">
                  {n.kind === "ci" ? `${n.class_name ?? "CI"} ${n.number}` : n.number}
                </text>
                <text x={14} y={35} fontSize={11.5} fill="var(--color-ink)">
                  {n.title.length > 26 ? `${n.title.slice(0, 25)}…` : n.title}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      {g.truncated && (
        <p className="text-xs text-muted" data-testid="link-graph-truncated">
          {t("itsm.graph.truncated")}
        </p>
      )}
    </div>
  );
}
