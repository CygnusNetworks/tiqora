import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { changeApi, changeKeys, type Change, type ChangeTask, type ChangeTaskKind } from "@/lib/changeApi";
import { changeErrorText, FIELD_CLASS, FINAL_PHASES, SECTION_CLASS, useChangeRefresh, windowText } from "./changeUi";

const KINDS: readonly ChangeTaskKind[] = ["workorder", "backout", "decision", "pir"];

function AddTaskForm({ onAdd, pending }: { onAdd: (title: string, kind: ChangeTaskKind) => void; pending: boolean }) {
  const { t } = useTranslation();
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<ChangeTaskKind>("workorder");
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (title.trim()) onAdd(title.trim(), kind);
      }}
      data-testid="change-task-new"
    >
      <label className="min-w-0 flex-1 basis-48 text-xs text-muted">
        {t("itsm.change.tasks.newTitle")}
        <input
          className={`${FIELD_CLASS} mt-1`}
          value={title}
          maxLength={255}
          onChange={(e) => setTitle(e.target.value)}
          data-testid="change-task-new-title"
        />
      </label>
      <div className="w-40 text-xs text-muted">
        <span>{t("itsm.change.tasks.kind")}</span>
        <SelectField<ChangeTaskKind>
          testId="change-task-new-kind"
          aria-label={t("itsm.change.tasks.kind")}
          className="mt-1"
          items={KINDS.map((k) => ({ value: k, label: t(`itsm.change.list.kinds.${k}`) }))}
          value={kind}
          onChange={setKind}
        />
      </div>
      <Button type="submit" size="sm" variant="primary" disabled={pending || !title.trim()} data-testid="change-task-new-submit">
        {t("itsm.change.tasks.addSubmit")}
      </Button>
    </form>
  );
}

/**
 * "Change-Aufgaben": the child tickets in their order, each linking to its ticket (where it is
 * worked on and closed). Writers add tasks and move them up or down; a final change is read-only.
 */
export function ChangeTasksSection({ change }: { change: Change }) {
  const { t } = useTranslation();
  const id = change.ticket_id;
  const refresh = useChangeRefresh(id);
  const [adding, setAdding] = useState(false);
  const canEdit = change.can_write && !FINAL_PHASES.has(change.phase);

  const q = useQuery({ queryKey: changeKeys.tasks(id), queryFn: ({ signal }) => changeApi.tasks(id, signal) });
  const tasks = [...(q.data ?? [])].sort((a, b) => a.position - b.position);

  const add = useMutation({
    mutationFn: ({ title, kind }: { title: string; kind: ChangeTaskKind }) => changeApi.createTask(id, { title, task_kind: kind }),
    onSuccess: () => {
      setAdding(false);
      refresh();
    },
  });
  const order = useMutation({
    mutationFn: (ids: number[]) => changeApi.orderTasks(id, ids),
    onSuccess: () => refresh(),
  });
  const move = (index: number, by: -1 | 1) => {
    const ids = tasks.map((task) => task.ticket_id);
    [ids[index], ids[index + by]] = [ids[index + by], ids[index]];
    order.mutate(ids);
  };
  const error = add.error ?? order.error ?? q.error;
  const done = tasks.filter((task) => task.finished).length;

  return (
    <section className={SECTION_CLASS} aria-label={t("itsm.change.tasks.title")} data-testid="change-tasks">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">{t("itsm.change.tasks.title")}</h3>
        <div className="flex items-center gap-2">
          {tasks.length > 0 && (
            <span className="text-xs text-muted" data-testid="change-tasks-progress">
              {t("itsm.change.tasks.progress", { done, total: tasks.length })}
            </span>
          )}
          {canEdit && !adding && (
            <Button size="sm" onClick={() => setAdding(true)} data-testid="change-tasks-add">
              {t("itsm.change.tasks.add")}
            </Button>
          )}
        </div>
      </div>
      {adding && <AddTaskForm pending={add.isPending} onAdd={(title, kind) => add.mutate({ title, kind })} />}
      {error && (
        <p className="text-xs text-danger" role="alert" data-testid="change-tasks-error">
          {changeErrorText(error, t)}
        </p>
      )}
      {q.isLoading ? (
        <Spinner />
      ) : tasks.length === 0 ? (
        !q.isError && (
          <p className="text-xs text-muted" data-testid="change-tasks-empty">
            {t("itsm.change.tasks.empty")}
          </p>
        )
      ) : (
        <ol className="divide-y divide-hairline">
          {tasks.map((task, i) => (
            <TaskRow
              key={task.ticket_id}
              task={task}
              index={i + 1}
              canMove={canEdit}
              first={i === 0}
              last={i === tasks.length - 1}
              busy={order.isPending}
              onMove={(by) => move(i, by)}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function TaskRow({
  task,
  index,
  canMove,
  first,
  last,
  busy,
  onMove,
}: {
  task: ChangeTask;
  /** the row number among the readable tasks (positions may skip hidden ones) */
  index: number;
  canMove: boolean;
  first: boolean;
  last: boolean;
  busy: boolean;
  onMove: (by: -1 | 1) => void;
}) {
  const { t } = useTranslation();
  const slot = windowText(task.planned_start, task.planned_end);
  return (
    <li className="flex flex-wrap items-center gap-2 py-1.5" data-testid="change-task" data-task-id={task.ticket_id}>
      <span className="w-5 shrink-0 text-right text-xs tabular-nums text-muted">{index}</span>
      <div className="min-w-0 flex-1">
        <RouterAnchor href={`/agent/tickets/${task.ticket_id}`} className="break-words text-accent hover:underline">
          <span className="font-mono text-xs">{task.tn}</span> {task.title}
        </RouterAnchor>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <Badge tone="muted">{t(`itsm.change.list.kinds.${task.task_kind}`)}</Badge>
          <Badge tone={task.finished ? "success" : "default"}>{task.state}</Badge>
          {slot && <span>{slot}</span>}
        </div>
      </div>
      {canMove && (
        <div className="flex gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={first || busy}
            aria-label={t("itsm.change.tasks.moveUp")}
            title={t("itsm.change.tasks.moveUp")}
            onClick={() => onMove(-1)}
            data-testid="change-task-up"
          >
            ↑
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={last || busy}
            aria-label={t("itsm.change.tasks.moveDown")}
            title={t("itsm.change.tasks.moveDown")}
            onClick={() => onMove(1)}
            data-testid="change-task-down"
          >
            ↓
          </Button>
        </div>
      )}
    </li>
  );
}
