import { useQuery } from "@tanstack/react-query";
import {
  processDesignerApi,
  type DesignerEntity,
  type EntitySummary,
  type ProcessDraft,
  type SubEntityType,
} from "@/lib/processDesignerApi";

/**
 * The shared entity library of the designer: steps, forms, transitions and actions are Znuny
 * entities that several processes may use (`GET /entities/{type}` with `used_by`). The draft of one
 * process only holds the entities that process references, so "Vorhandene verwenden" lists come from
 * here, and a picked entity is merged into the cached draft (`mergeEntity`) before it is used.
 */
export const ENTITY_LIBRARY_KEY = ["admin", "process-designer", "entities"] as const;
export const PROCESS_LIST_KEY = ["admin", "process-designer", "processes"] as const;

const DRAFT_LIST = {
  Activity: "activities",
  ActivityDialog: "activity_dialogs",
  Transition: "transitions",
  TransitionAction: "transition_actions",
} as const satisfies Record<SubEntityType, keyof ProcessDraft>;

/** The draft with `entity` added to (or replaced in) the list of its type. */
export function mergeEntity(draft: ProcessDraft, entity: DesignerEntity): ProcessDraft {
  const key = DRAFT_LIST[entity.entity_type as SubEntityType];
  const list = draft[key];
  const has = list.some((e) => e.entity_id === entity.entity_id);
  return {
    ...draft,
    [key]: has ? list.map((e) => (e.entity_id === entity.entity_id ? entity : e)) : [...list, entity],
  };
}

/** True when the draft already holds that entity (no fetch needed). */
export function draftHas(draft: ProcessDraft, type: SubEntityType, entityId: string): boolean {
  return draft[DRAFT_LIST[type]].some((e) => e.entity_id === entityId);
}

/**
 * Who else uses an entity: process ids for steps, transitions and actions (minus this process),
 * activity ids for forms (minus the steps of this process). Names come from `names`.
 */
export function otherUsers(
  summary: EntitySummary | undefined,
  own: Set<string>,
  names: Map<string, string>,
): string[] {
  if (!summary) return [];
  return summary.used_by.filter((id) => !own.has(id)).map((id) => names.get(id) ?? id);
}

function useEntityList(type: SubEntityType) {
  return useQuery({
    queryKey: [...ENTITY_LIBRARY_KEY, type],
    queryFn: ({ signal }) => processDesignerApi.listEntities(type, signal),
  });
}

export type EntityLibrary = Record<SubEntityType, EntitySummary[]> & {
  /** Process names by entity id (for "used by" hints). */
  processNames: Map<string, string>;
};

export function useEntityLibrary(): EntityLibrary {
  const activities = useEntityList("Activity");
  const dialogs = useEntityList("ActivityDialog");
  const transitions = useEntityList("Transition");
  const actions = useEntityList("TransitionAction");
  const processes = useQuery({
    queryKey: PROCESS_LIST_KEY,
    queryFn: ({ signal }) => processDesignerApi.listProcesses(signal),
  });
  return {
    Activity: activities.data ?? [],
    ActivityDialog: dialogs.data ?? [],
    Transition: transitions.data ?? [],
    TransitionAction: actions.data ?? [],
    processNames: new Map((processes.data ?? []).map((p) => [p.entity_id, p.name])),
  };
}
