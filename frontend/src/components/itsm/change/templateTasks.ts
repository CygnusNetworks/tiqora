/** Pure helpers of the change template's task list (`TemplateTask`): checks, defaults, free keys. */
import type { ChangeTemplateTask } from "@/lib/changeAdminApi";

export const TASK_KINDS: ChangeTemplateTask["task_kind"][] = ["workorder", "backout", "decision", "pir"];
/** `TemplateTask.key` on the backend. */
export const TASK_KEY_PATTERN = /^[a-z0-9_]{1,50}$/;
/** `TemplateIn.tasks` holds at most this many (the setting `max_tasks` may be lower; the save says so). */
export const MAX_TEMPLATE_TASKS = 200;
export const OFFSET_RANGE = [-43200, 525600] as const;
export const DURATION_RANGE = [1, 525600] as const;

export type TaskProblem = "key" | "keyDuplicate" | "title" | "offset" | "duration";

/** What is wrong with each task (same index); an empty list means the task can be saved. */
export function templateTaskProblems(tasks: ChangeTemplateTask[]): TaskProblem[][] {
  const seen = new Map<string, number>();
  for (const task of tasks) seen.set(task.key, (seen.get(task.key) ?? 0) + 1);
  return tasks.map((task) => {
    const out: TaskProblem[] = [];
    if (!TASK_KEY_PATTERN.test(task.key)) out.push("key");
    else if ((seen.get(task.key) ?? 0) > 1) out.push("keyDuplicate");
    if (task.title.trim() === "") out.push("title");
    const offset = task.offset_minutes ?? 0;
    if (offset < OFFSET_RANGE[0] || offset > OFFSET_RANGE[1]) out.push("offset");
    const duration = task.duration_minutes;
    if (duration != null && (duration < DURATION_RANGE[0] || duration > DURATION_RANGE[1])) out.push("duration");
    return out;
  });
}

/** A stored task with every optional field filled, so the editor and the save body agree. */
export function normalizeTask(task: ChangeTemplateTask, index: number): ChangeTemplateTask {
  return {
    key: task.key,
    title: task.title,
    task_kind: task.task_kind ?? "workorder",
    offset_minutes: task.offset_minutes ?? 0,
    duration_minutes: task.duration_minutes ?? null,
    queue_id: task.queue_id ?? null,
    owner_id: task.owner_id ?? null,
    body: task.body ?? "",
    position: index + 1,
  };
}

export function freeKey(tasks: ChangeTemplateTask[]): string {
  const taken = new Set(tasks.map((t) => t.key));
  let n = tasks.length + 1;
  while (taken.has(`task_${n}`)) n += 1;
  return `task_${n}`;
}
