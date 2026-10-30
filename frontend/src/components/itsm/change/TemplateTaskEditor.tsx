import { useTranslation } from "react-i18next";
import { AgentSelect } from "@/components/approvals/pickers";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { useRowKeys, newRowKey } from "@/components/approvals/rowKeys";
import { intOrNull } from "./changeAdminUi";
import { FIELD_CLASS, LABEL_CLASS } from "./changeUi";
import { DURATION_RANGE, MAX_TEMPLATE_TASKS, OFFSET_RANGE, TASK_KINDS, freeKey, renumber, templateTaskProblems, type TaskDraft } from "./templateTasks";


/**
 * The tasks a change template creates (`TemplateTask`): key, title, kind, start relative to the
 * change, duration, queue, owner and description, in order. Positions are renumbered 1..n on every
 * change. `showProblems` marks invalid tasks (after a first save attempt).
 */
export function TemplateTaskEditor({
  value,
  onChange,
  queues,
  showProblems,
}: {
  value: TaskDraft[];
  onChange: (next: TaskDraft[]) => void;
  queues: { id: number; name: string }[];
  showProblems: boolean;
}) {
  const { t } = useTranslation();
  const [keys, setKeys] = useRowKeys(value.length);
  const emit = (next: TaskDraft[], nextKeys = keys) => {
    setKeys(nextKeys);
    onChange(renumber(next));
  };
  const patch = (i: number, p: Partial<TaskDraft>) => emit(value.map((task, idx) => (idx === i ? { ...task, ...p } : task)));
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    const swap = <T,>(list: T[]) => list.map((x, idx) => (idx === i ? list[j] : idx === j ? list[i] : x));
    emit(swap(value), swap(keys));
  };
  const problems = templateTaskProblems(value);
  const queueItems = [
    { value: 0, label: t("admin.itsmChange.tasks.queueDefault") },
    ...queues.map((q) => ({ value: q.id, label: q.name })),
  ];
  const kindItems = TASK_KINDS.map((k) => ({ value: k, label: t(`itsm.change.list.kinds.${k}`) }));

  return (
    <div className="space-y-3" data-testid="template-task-editor">
      {value.length === 0 && (
        <p className="text-sm text-muted" data-testid="template-tasks-empty">
          {t("admin.itsmChange.tasks.empty")}
        </p>
      )}
      <ol className="space-y-3">
        {value.map((task, i) => {
          const marked = showProblems ? problems[i] : [];
          const n = i + 1;
          return (
            <li
              key={keys[i]}
              className="min-w-0 space-y-2 rounded-md border border-hairline bg-surface p-3"
              data-testid="template-task-row"
              aria-label={t("admin.itsmChange.tasks.row", { n })}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-xs text-muted">{n}.</span>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" disabled={i === 0} onClick={() => move(i, -1)} aria-label={t("admin.itsmChange.moveUp")} data-testid={`template-task-up-${i}`}>
                    ↑
                  </Button>
                  <Button size="sm" variant="ghost" disabled={i === value.length - 1} onClick={() => move(i, 1)} aria-label={t("admin.itsmChange.moveDown")} data-testid={`template-task-down-${i}`}>
                    ↓
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => emit(value.filter((_, idx) => idx !== i), keys.filter((_, idx) => idx !== i))}
                    aria-label={t("admin.itsmChange.tasks.remove", { n })}
                    data-testid={`template-task-remove-${i}`}
                  >
                    ✕
                  </Button>
                </div>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                <label className="min-w-0">
                  <span className={LABEL_CLASS}>{t("admin.itsmChange.tasks.key")}</span>
                  <input
                    className={`${FIELD_CLASS} font-mono`}
                    value={task.key}
                    maxLength={50}
                    onChange={(e) => patch(i, { key: e.target.value })}
                    aria-invalid={marked.includes("key") || marked.includes("keyDuplicate")}
                    data-testid={`template-task-key-${i}`}
                  />
                </label>
                <label className="min-w-0 sm:col-span-2">
                  <span className={LABEL_CLASS}>{t("admin.itsmChange.tasks.title")}</span>
                  <input
                    className={FIELD_CLASS}
                    value={task.title}
                    maxLength={255}
                    onChange={(e) => patch(i, { title: e.target.value })}
                    aria-invalid={marked.includes("title")}
                    data-testid={`template-task-title-${i}`}
                  />
                </label>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                <div className="min-w-0">
                  <span className={LABEL_CLASS}>{t("admin.itsmChange.tasks.kind")}</span>
                  <SelectField
                    items={kindItems}
                    value={task.task_kind ?? "workorder"}
                    onChange={(task_kind) => patch(i, { task_kind })}
                    testId={`template-task-kind-${i}`}
                    aria-label={t("admin.itsmChange.tasks.kind")}
                  />
                </div>
                <label className="min-w-0">
                  <span className={LABEL_CLASS}>{t("admin.itsmChange.tasks.offset")}</span>
                  <input
                    type="number"
                    className={FIELD_CLASS}
                    value={task.offset_minutes ?? ""}
                    placeholder="0"
                    min={OFFSET_RANGE[0]}
                    max={OFFSET_RANGE[1]}
                    onChange={(e) => patch(i, { offset_minutes: intOrNull(e.target.value) })}
                    aria-invalid={marked.includes("offset")}
                    data-testid={`template-task-offset-${i}`}
                  />
                </label>
                <label className="min-w-0">
                  <span className={LABEL_CLASS}>{t("admin.itsmChange.tasks.duration")}</span>
                  <input
                    type="number"
                    className={FIELD_CLASS}
                    value={task.duration_minutes ?? ""}
                    min={DURATION_RANGE[0]}
                    max={DURATION_RANGE[1]}
                    onChange={(e) => patch(i, { duration_minutes: intOrNull(e.target.value) })}
                    aria-invalid={marked.includes("duration")}
                    data-testid={`template-task-duration-${i}`}
                  />
                </label>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <div className="min-w-0">
                  <span className={LABEL_CLASS}>{t("admin.itsmChange.tasks.queue")}</span>
                  <SelectField
                    items={queueItems}
                    value={task.queue_id ?? 0}
                    onChange={(id) => patch(i, { queue_id: id === 0 ? null : id })}
                    testId={`template-task-queue-${i}`}
                    aria-label={t("admin.itsmChange.tasks.queue")}
                  />
                </div>
                <div className="min-w-0">
                  <span className={LABEL_CLASS}>{t("admin.itsmChange.tasks.owner")}</span>
                  <div className="flex min-w-0 gap-1">
                    <div className="min-w-0 flex-1">
                      <AgentSelect
                        value={task.owner_id ?? null}
                        onChange={(id) => patch(i, { owner_id: id })}
                        testId={`template-task-owner-${i}`}
                        ariaLabel={t("admin.itsmChange.tasks.owner")}
                      />
                    </div>
                    {task.owner_id != null && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => patch(i, { owner_id: null })}
                        aria-label={t("admin.itsmChange.tasks.clearOwner")}
                        data-testid={`template-task-owner-clear-${i}`}
                      >
                        ✕
                      </Button>
                    )}
                  </div>
                </div>
              </div>
              <label className="block min-w-0">
                <span className={LABEL_CLASS}>{t("admin.itsmChange.tasks.body")}</span>
                <textarea
                  className={FIELD_CLASS}
                  rows={2}
                  maxLength={20000}
                  value={task.body ?? ""}
                  onChange={(e) => patch(i, { body: e.target.value })}
                  data-testid={`template-task-body-${i}`}
                />
              </label>
              {marked.length > 0 && (
                <ul className="space-y-0.5 text-xs text-danger" role="alert" data-testid={`template-task-problems-${i}`}>
                  {marked.map((p) => (
                    <li key={p}>{t(`admin.itsmChange.tasks.problems.${p}`)}</li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={value.length >= MAX_TEMPLATE_TASKS}
          onClick={() =>
            emit(
              [...value, { key: freeKey(value), title: "", task_kind: "workorder", offset_minutes: 0, duration_minutes: null, queue_id: null, owner_id: null, body: "", position: 0 }],
              [...keys, newRowKey()],
            )
          }
          data-testid="template-task-add"
        >
          {t("admin.itsmChange.tasks.add")}
        </Button>
        {value.length >= MAX_TEMPLATE_TASKS && (
          <span className="text-xs text-muted">{t("admin.itsmChange.tasks.max", { max: MAX_TEMPLATE_TASKS })}</span>
        )}
      </div>
    </div>
  );
}
