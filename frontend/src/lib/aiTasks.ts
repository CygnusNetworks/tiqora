/**
 * The six AI tasks a model profile can be assigned to — shared by the
 * "KI → Modelle" page (global defaults) and the queue editor (per-queue
 * overrides). Mirrors `backend/src/tiqora/ai/llm_routing.py` (`TASK_NEEDS`,
 * `TASKS_FALLING_BACK_TO_AGENT`) and the resolution rules of
 * `resolve_task_profile_id`.
 */
import type { AiTaskProfileItem } from "./aiApi";

export const AI_TASKS = [
  "agent",
  "final_answer",
  "triage",
  "summary",
  "refine",
  "vision",
] as const;

export type AiTask = (typeof AI_TASKS)[number];

export type AiTaskNeed = "tools" | "vision";

/** What every model of a profile must be able to do for the task. */
export const AI_TASK_NEEDS: Record<AiTask, readonly AiTaskNeed[]> = {
  agent: ["tools"],
  final_answer: ["tools"],
  triage: ["tools"],
  summary: [],
  refine: [],
  vision: ["vision"],
};

/** Tasks that run on the agent's profile when they resolve to none of their
 * own. The others then do without: no AI (agent), the agent answers itself
 * (final_answer), images are ignored (vision). */
export const AI_TASKS_FALLING_BACK_TO_AGENT: ReadonlySet<AiTask> = new Set([
  "triage",
  "summary",
  "refine",
]);

export function isAiTask(value: string): value is AiTask {
  return (AI_TASKS as readonly string[]).includes(value);
}

/** UI name, e.g. "Recherche und Werkzeuge". */
export const aiTaskNameKey = (task: AiTask) => `admin.ai.tasks.name.${task}`;
/** One-line description of what the task does. */
export const aiTaskDescriptionKey = (task: AiTask) =>
  `admin.ai.tasks.description.${task}`;
/** What happens without a profile, e.g. "Bilder werden ignoriert". */
export const aiTaskFallbackKey = (task: AiTask) =>
  `admin.ai.tasks.fallback.${task}`;
/** Capability chip label ("Werkzeuge" / "Bilder"). */
export const aiNeedLabelKey = (need: AiTaskNeed) =>
  `admin.ai.models.cap.${need}`;
/** "kann keine Werkzeuge nutzen" / "kann keine Bilder lesen". */
export const aiNeedMissingKey = (need: AiTaskNeed) =>
  `admin.ai.tasks.missing.${need}`;

type Capable = { supports_tools: boolean; supports_vision: boolean };

/** Needs of *task* that at least one model in *models* does not meet — the
 * backend rejects such an assignment, so the UI disables the option. */
export function missingNeeds(task: AiTask, models: Capable[]): AiTaskNeed[] {
  return AI_TASK_NEEDS[task].filter((need) =>
    models.some((m) =>
      need === "tools" ? !m.supports_tools : !m.supports_vision,
    ),
  );
}

/** Assignment list → map; a task that is absent stays absent (= inherits). */
export function taskProfileMap(
  items: AiTaskProfileItem[] | undefined,
): Map<AiTask, number | null> {
  const out = new Map<AiTask, number | null>();
  for (const item of items ?? []) {
    if (isAiTask(item.task)) out.set(item.task, item.profile_id);
  }
  return out;
}

/**
 * Profile a task actually runs on, following the backend rules: the queue's
 * own row wins (null = "Kein eigenes Profil"), else the global default; a
 * disabled profile counts as none; triage/summary/refine then use the agent's
 * profile. `queue` = null resolves the global level only.
 */
export function resolveTaskProfile(
  task: AiTask,
  globalDefaults: Map<AiTask, number | null>,
  queue: Map<AiTask, number | null> | null,
  isActive: (profileId: number) => boolean,
): number | null {
  const own = (t: AiTask): number | null => {
    const id = queue?.has(t)
      ? (queue.get(t) ?? null)
      : (globalDefaults.get(t) ?? null);
    return id != null && isActive(id) ? id : null;
  };
  const id = own(task);
  if (id == null && AI_TASKS_FALLING_BACK_TO_AGENT.has(task)) {
    return own("agent");
  }
  return id;
}
