import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useTranslation } from "react-i18next";
import type { EntitySummary, ProcessDraft, SubEntityType, ValidationIssue } from "@/lib/processDesignerApi";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { Button } from "@/components/ui/Button";
import { ActivityNode, type ActivityFlowNode } from "./ActivityNode";
import { TransitionEdge, type TransitionFlowEdge } from "./TransitionEdge";
import { PALETTE_DRAG_TYPE } from "./EntityPalette";
import {
  addActivity,
  connect,
  disconnect,
  move,
  removeActivity,
  toCanvas,
  type ProcessShape,
} from "./canvasModel";

export type Selection =
  | { kind: "activity"; id: string }
  | { kind: "transition"; from: string; transitionId: string }
  | null;

const nodeTypes = { activity: ActivityNode };
const edgeTypes = { transition: TransitionEdge };

// The controls and minimap follow the theme tokens (light/dark) through CSS variables.
const themeVars = {
  "--xy-background-color": "var(--color-surface)",
  "--xy-node-border-radius": "8px",
  "--xy-controls-button-background-color": "var(--color-surface)",
  "--xy-controls-button-background-color-hover": "var(--color-surface-subtle)",
  "--xy-controls-button-color": "var(--color-ink)",
  "--xy-controls-button-border-color": "var(--color-hairline)",
  "--xy-minimap-background-color": "var(--color-surface-subtle)",
  "--xy-edge-stroke": "var(--color-muted)",
  "--xy-attribution-background-color": "transparent",
} as React.CSSProperties;

export type DesignerCanvasProps = {
  /** The loaded draft; names and entities come from here. */
  draft: ProcessDraft;
  /** The local, possibly unsaved, shape (path, layout, start). */
  shape: ProcessShape;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onShapeChange: (s: ProcessShape) => void;
  /** Creates a new, empty activity entity and resolves to its entity id. */
  createActivity: () => Promise<string>;
  /** Creates a new transition entity with that name and resolves to its entity id. */
  createTransition: (name: string) => Promise<string>;
  /** Every transition of all processes (shared entities) for "Vorhandene verwenden" on connect. */
  transitions?: EntitySummary[];
  /** Loads an existing entity of another process into the draft before it is placed. */
  adoptEntity?: (type: SubEntityType, id: string) => Promise<void>;
  /** Latest check result; the issue counts show as badges on steps and transitions. */
  issues?: ValidationIssue[];
};

/** The draft as `toCanvas` expects it, with the local shape laid over the stored one. */
function withShape(draft: ProcessDraft, shape: ProcessShape): ProcessDraft {
  return {
    ...draft,
    layout: shape.layout as ProcessDraft["layout"],
    process: {
      ...draft.process,
      config: {
        ...draft.process.config,
        Path: shape.path,
        StartActivity: shape.startActivity,
        StartActivityDialog: shape.startActivityDialog,
      },
    },
  };
}

function Inner({
  draft,
  shape,
  selection,
  onSelect,
  onShapeChange,
  createActivity,
  createTransition,
  transitions,
  adoptEntity,
  issues,
}: DesignerCanvasProps) {
  const { t } = useTranslation();
  const { screenToFlowPosition } = useReactFlow();
  const { confirm, dialog } = useConfirm();
  const [pending, setPending] = useState<{ source: string; target: string } | null>(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState(false);
  // The handlers below await the server; they must apply their edit to the shape as it is then,
  // not to the one captured when they started (an edit made meanwhile would be lost).
  const shapeRef = useRef(shape);
  useLayoutEffect(() => {
    shapeRef.current = shape;
  }, [shape]);

  const model = useMemo(() => toCanvas(withShape(draft, shape), issues ?? []), [draft, shape, issues]);

  const modelNodes = useMemo<ActivityFlowNode[]>(
    () =>
      model.nodes.map((n) => ({
        ...n,
        type: "activity" as const,
        selected: selection?.kind === "activity" && selection.id === n.id,
      })),
    [model.nodes, selection],
  );
  const modelEdges = useMemo<TransitionFlowEdge[]>(
    () =>
      model.edges.map((e) => ({
        ...e,
        type: "transition" as const,
        markerEnd: { type: "arrowclosed" as const },
        selected:
          selection?.kind === "transition" &&
          selection.from === e.source &&
          selection.transitionId === e.data.transitionId,
      })),
    [model.edges, selection],
  );

  // Dragging moves a local copy; the position is committed to the shape on drag stop.
  const [nodes, setNodes] = useState<Node[]>(modelNodes);
  useEffect(() => setNodes(modelNodes), [modelNodes]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((cur) =>
      changes.reduce((acc, c) => {
        if (c.type === "position" && c.position) {
          const position = c.position;
          return acc.map((n) => (n.id === c.id ? { ...n, position } : n));
        }
        return acc;
      }, cur),
    );
  }, []);

  const onConnect = useCallback((c: Connection) => {
    if (c.source && c.target) {
      setNewName("");
      setPending({ source: c.source, target: c.target });
    }
  }, []);

  const finishConnect = (transitionId: string) => {
    if (!pending) return;
    onShapeChange(connect(shapeRef.current, pending.source, transitionId, pending.target));
    onSelect({ kind: "transition", from: pending.source, transitionId });
    setPending(null);
  };

  const connectNew = async () => {
    const name = newName.trim() || t("admin.processDesigner.newTransition");
    setBusy(true);
    setCreateError(false);
    try {
      finishConnect(await createTransition(name));
    } catch {
      setCreateError(true);
    } finally {
      setBusy(false);
    }
  };

  const connectExisting = async (transitionId: string) => {
    setBusy(true);
    setCreateError(false);
    try {
      await adoptEntity?.("Transition", transitionId);
      finishConnect(transitionId);
    } catch {
      setCreateError(true);
    } finally {
      setBusy(false);
    }
  };

  const onDragOver = (e: React.DragEvent) => {
    if (e.dataTransfer.types.includes(PALETTE_DRAG_TYPE)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  };
  const onDrop = async (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes(PALETTE_DRAG_TYPE)) return;
    e.preventDefault();
    const payload = e.dataTransfer.getData(PALETTE_DRAG_TYPE);
    const at = screenToFlowPosition({ x: e.clientX, y: e.clientY });
    setCreateError(false);
    let id = payload;
    try {
      if (id) await adoptEntity?.("Activity", id);
      else id = await createActivity();
    } catch {
      setCreateError(true);
      return;
    }
    onShapeChange(addActivity(shapeRef.current, id, at));
    onSelect({ kind: "activity", id });
  };

  const onKeyDown = async (e: React.KeyboardEvent) => {
    if (e.key !== "Delete" || !selection) return;
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    e.preventDefault();
    if (selection.kind === "activity") {
      const name = model.nodes.find((n) => n.id === selection.id)?.data.name ?? selection.id;
      const ok = await confirm({
        title: t("admin.processDesigner.delete"),
        message: t("admin.processDesigner.deleteStep", { name }),
        confirmLabel: t("admin.processDesigner.delete"),
        variant: "danger",
      });
      if (ok) {
        onShapeChange(removeActivity(shape, selection.id));
        onSelect(null);
      }
    } else {
      const name =
        model.edges.find((x) => x.source === selection.from && x.data.transitionId === selection.transitionId)?.data
          .name ?? selection.transitionId;
      const ok = await confirm({
        title: t("admin.processDesigner.delete"),
        message: t("admin.processDesigner.deleteTransition", { name }),
        confirmLabel: t("admin.processDesigner.delete"),
        variant: "danger",
      });
      if (ok) {
        onShapeChange(disconnect(shape, selection.from, selection.transitionId));
        onSelect(null);
      }
    }
  };

  const reusable = pending
    ? (transitions ?? draft.transitions).filter((tr) => !(tr.entity_id in (shape.path[pending.source] ?? {})))
    : [];

  return (
    <div
      className="relative h-full min-h-[420px] w-full"
      style={themeVars}
      data-testid="process-designer-canvas"
      tabIndex={-1}
      onKeyDown={onKeyDown}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <ReactFlow
        nodes={nodes}
        edges={modelEdges as Edge[]}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStop={(_, node) => onShapeChange(move(shape, node.id, node.position))}
        onConnect={onConnect}
        onNodeClick={(_, node) => onSelect({ kind: "activity", id: node.id })}
        onEdgeClick={(_, edge) => {
          const data = edge.data as TransitionFlowEdge["data"];
          onSelect({ kind: "transition", from: edge.source, transitionId: data?.transitionId ?? "" });
        }}
        onPaneClick={() => {
          onSelect(null);
          setPending(null);
        }}
        deleteKeyCode={null}
        nodesConnectable
        elementsSelectable
        fitView
      >
        <Background />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable maskColor="color-mix(in srgb, var(--color-ink) 12%, transparent)" />
      </ReactFlow>

      {createError ? (
        <p
          role="alert"
          data-testid="process-designer-create-error"
          className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-md border border-danger bg-surface px-3 py-1.5 text-sm text-danger"
        >
          {t("admin.processDesigner.createError")}
        </p>
      ) : null}
      {pending ? (
        <div
          role="dialog"
          aria-label={t("admin.processDesigner.newTransition")}
          data-testid="process-designer-connect-popover"
          className="absolute left-1/2 top-3 z-10 w-72 -translate-x-1/2 space-y-2 rounded-lg border border-hairline bg-surface p-3 shadow-md"
        >
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={t("admin.processDesigner.transitionName")}
            aria-label={t("admin.processDesigner.transitionName")}
            className="w-full rounded-md border border-hairline bg-surface px-2 py-1 text-sm text-ink"
          />
          <Button variant="primary" size="sm" disabled={busy} onClick={connectNew} data-testid="process-designer-connect-new">
            {t("admin.processDesigner.newTransition")}
          </Button>
          {reusable.length > 0 ? (
            <div>
              <p className="mb-1 text-xs text-muted">{t("admin.processDesigner.reuse")}</p>
              <ul className="max-h-32 space-y-1 overflow-auto">
                {reusable.map((tr) => (
                  <li key={tr.entity_id}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void connectExisting(tr.entity_id)}
                      data-testid={`process-designer-connect-existing-${tr.entity_id}`}
                      className="w-full rounded px-2 py-1 text-left text-sm text-ink hover:bg-surface-subtle"
                    >
                      {tr.name}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => setPending(null)}>
            {t("common.cancel")}
          </Button>
        </div>
      ) : null}
      {dialog}
    </div>
  );
}

export function DesignerCanvas(props: DesignerCanvasProps) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}
