// Maps a validation issue to what the designer should select for it. Pure, no React.
import type { ProcessDraft, ValidationIssue } from "@/lib/processDesignerApi";
import type { Selection } from "./DesignerCanvas";
import type { ProcessShape } from "./canvasModel";

export type IssueTarget = { selection: Selection; actionId: string | null };

function transitionsOf(shape: ProcessShape): { from: string; transitionId: string; actions: string[] }[] {
  const out: { from: string; transitionId: string; actions: string[] }[] = [];
  for (const [from, entries] of Object.entries(shape.path ?? {})) {
    for (const [transitionId, def] of Object.entries((entries ?? {}) as Record<string, unknown>)) {
      const raw = (def as { TransitionAction?: unknown } | undefined)?.TransitionAction;
      out.push({
        from,
        transitionId,
        actions: Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [],
      });
    }
  }
  return out;
}

/** The canvas/panel selection an issue points at; process-level issues select nothing (process panel). */
export function targetOfIssue(issue: ValidationIssue, draft: ProcessDraft, shape: ProcessShape): IssueTarget {
  const none: IssueTarget = { selection: null, actionId: null };
  switch (issue.entity_type) {
    case "Activity":
      return { selection: { kind: "activity", id: issue.entity_id }, actionId: null };
    case "Transition": {
      const hit = transitionsOf(shape).find((x) => x.transitionId === issue.entity_id);
      return hit ? { selection: { kind: "transition", from: hit.from, transitionId: hit.transitionId }, actionId: null } : none;
    }
    case "TransitionAction": {
      const hit = transitionsOf(shape).find((x) => x.actions.includes(issue.entity_id));
      return hit
        ? { selection: { kind: "transition", from: hit.from, transitionId: hit.transitionId }, actionId: issue.entity_id }
        : none;
    }
    case "ActivityDialog": {
      const owner = draft.activities.find((a) => {
        const offered = (a.config as { ActivityDialog?: unknown }).ActivityDialog;
        return offered && typeof offered === "object" && Object.values(offered).includes(issue.entity_id);
      });
      return owner ? { selection: { kind: "activity", id: owner.entity_id }, actionId: null } : none;
    }
    default:
      return none;
  }
}
