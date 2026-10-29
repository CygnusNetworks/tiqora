import { useState } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { serveChangeAdmin, useGerman } from "@/routes/admin/itsm/changeAdminTestUtils";
import { renderWithClient } from "./changeTestSupport";
import { TemplateTaskEditor } from "./TemplateTaskEditor";
import { taskToSave, templateTaskProblems, type TaskDraft } from "./templateTasks";

beforeAll(useGerman);
afterEach(() => vi.restoreAllMocks());

const task = (key: string, over: Partial<TaskDraft> = {}): TaskDraft => ({
  key,
  title: `Aufgabe ${key}`,
  task_kind: "workorder",
  offset_minutes: 0,
  duration_minutes: null,
  queue_id: null,
  owner_id: null,
  body: "",
  position: 0,
  ...over,
});

const QUEUES = [
  { id: 1, name: "Raw" },
  { id: 5, name: "Betrieb" },
];

function Harness({ initial, onChange, showProblems = false }: { initial: TaskDraft[]; onChange: (v: TaskDraft[]) => void; showProblems?: boolean }) {
  const [value, setValue] = useState(initial);
  return (
    <TemplateTaskEditor
      value={value}
      queues={QUEUES}
      showProblems={showProblems}
      onChange={(next) => {
        setValue(next);
        onChange(next);
      }}
    />
  );
}

describe("templateTaskProblems", () => {
  it("names a bad key, a key used twice, an empty title and out-of-range minutes", () => {
    const problems = templateTaskProblems([
      task("ok"),
      task("Bad-Key"),
      task("twice"),
      task("twice", { title: "  " }),
      task("late", { offset_minutes: 600000, duration_minutes: 0 }),
    ]);
    expect(problems[0]).toEqual([]);
    expect(problems[1]).toEqual(["key"]);
    expect(problems[2]).toEqual(["keyDuplicate"]);
    expect(problems[3]).toEqual(["keyDuplicate", "title"]);
    expect(problems[4]).toEqual(["offset", "duration"]);
  });
});

describe("TemplateTaskEditor", () => {
  it("adds a task with a free key and renumbers the positions", () => {
    serveChangeAdmin();
    const onChange = vi.fn();
    renderWithClient(<Harness initial={[task("task_1"), task("backup")]} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("template-task-add"));
    const next = onChange.mock.calls.at(-1)?.[0] as TaskDraft[];
    expect(next.map((t) => t.key)).toEqual(["task_1", "backup", "task_3"]);
    expect(next.map((t) => t.position)).toEqual([1, 2, 3]);
    expect(next[2]).toMatchObject({ title: "", task_kind: "workorder", offset_minutes: 0, duration_minutes: null });
    expect(screen.getAllByTestId("template-task-row")).toHaveLength(3);
  });

  it("edits the fields of a task: key, title, kind, minutes, queue and description", () => {
    serveChangeAdmin();
    const onChange = vi.fn();
    renderWithClient(<Harness initial={[task("apply")]} onChange={onChange} />);
    fireEvent.change(screen.getByTestId("template-task-key-0"), { target: { value: "apply_fw" } });
    fireEvent.change(screen.getByTestId("template-task-title-0"), { target: { value: "Firmware einspielen" } });
    fireEvent.change(screen.getByTestId("template-task-offset-0"), { target: { value: "-30" } });
    fireEvent.change(screen.getByTestId("template-task-duration-0"), { target: { value: "90" } });
    fireEvent.change(screen.getByTestId("template-task-body-0"), { target: { value: "Schritt für Schritt" } });
    fireEvent.click(screen.getByTestId("template-task-kind-0"));
    fireEvent.click(screen.getByTestId("template-task-kind-0-menu-option-backout"));
    fireEvent.click(screen.getByTestId("template-task-queue-0"));
    fireEvent.click(screen.getByTestId("template-task-queue-0-menu-option-5"));
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual([
      {
        key: "apply_fw",
        title: "Firmware einspielen",
        task_kind: "backout",
        offset_minutes: -30,
        duration_minutes: 90,
        queue_id: 5,
        owner_id: null,
        body: "Schritt für Schritt",
        position: 1,
      },
    ]);
    // An empty duration means "no duration", the queue can go back to the change's queue.
    fireEvent.change(screen.getByTestId("template-task-duration-0"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("template-task-queue-0"));
    fireEvent.click(screen.getByTestId("template-task-queue-0-menu-option-0"));
    expect(onChange.mock.calls.at(-1)?.[0][0]).toMatchObject({ duration_minutes: null, queue_id: null });
  });

  it("lets the start offset be cleared or start with a minus sign while typing", () => {
    serveChangeAdmin();
    const onChange = vi.fn();
    renderWithClient(<Harness initial={[task("backup", { offset_minutes: -60 })]} onChange={onChange} />);
    const offset = screen.getByTestId("template-task-offset-0") as HTMLInputElement;
    expect(offset.value).toBe("-60");
    // A cleared field (and a lone "-", which a number input reports as "") stays empty, never "0".
    fireEvent.change(offset, { target: { value: "" } });
    expect(offset.value).toBe("");
    expect(onChange.mock.calls.at(-1)?.[0][0].offset_minutes).toBeNull();
    fireEvent.change(offset, { target: { value: "-30" } });
    expect(offset.value).toBe("-30");
    expect(onChange.mock.calls.at(-1)?.[0][0].offset_minutes).toBe(-30);
  });

  it("saves an empty start offset as the change's start", () => {
    expect(taskToSave(task("a", { offset_minutes: null }), 0)).toMatchObject({ offset_minutes: 0, position: 1 });
    expect(taskToSave(task("b", { offset_minutes: -30 }), 1)).toMatchObject({ offset_minutes: -30, position: 2 });
  });

  it("picks and clears an owner", async () => {
    serveChangeAdmin();
    const onChange = vi.fn();
    renderWithClient(<Harness initial={[task("apply")]} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("template-task-owner-0"));
    fireEvent.click(await screen.findByTestId("template-task-owner-0-menu-option-3"));
    expect(onChange.mock.calls.at(-1)?.[0][0].owner_id).toBe(3);
    fireEvent.click(screen.getByTestId("template-task-owner-clear-0"));
    expect(onChange.mock.calls.at(-1)?.[0][0].owner_id).toBeNull();
  });

  it("moves and removes tasks", () => {
    serveChangeAdmin();
    const onChange = vi.fn();
    renderWithClient(<Harness initial={[task("a"), task("b"), task("c")]} onChange={onChange} />);
    expect(screen.getByTestId("template-task-up-0")).toBeDisabled();
    expect(screen.getByTestId("template-task-down-2")).toBeDisabled();
    fireEvent.click(screen.getByTestId("template-task-down-0"));
    expect((onChange.mock.calls.at(-1)?.[0] as TaskDraft[]).map((t) => t.key)).toEqual(["b", "a", "c"]);
    fireEvent.click(screen.getByTestId("template-task-remove-2"));
    const next = onChange.mock.calls.at(-1)?.[0] as TaskDraft[];
    expect(next.map((t) => [t.key, t.position])).toEqual([
      ["b", 1],
      ["a", 2],
    ]);
  });

  it("marks the problems of a task once asked to", () => {
    serveChangeAdmin();
    const quiet = renderWithClient(<Harness initial={[task("x"), task("x", { title: "" })]} onChange={vi.fn()} />);
    expect(screen.queryByTestId("template-task-problems-1")).toBeNull();
    quiet.unmount();
    renderWithClient(<Harness initial={[task("x"), task("x", { title: "" })]} onChange={vi.fn()} showProblems />);
    const problems = screen.getByTestId("template-task-problems-1");
    expect(within(problems).getByText("Dieser Schlüssel kommt mehrfach vor.")).toBeInTheDocument();
    expect(within(problems).getByText("Bitte geben Sie einen Titel ein.")).toBeInTheDocument();
    expect(screen.getByTestId("template-task-key-1")).toHaveAttribute("aria-invalid", "true");
  });

  it("says when the template has no tasks", () => {
    serveChangeAdmin();
    renderWithClient(<Harness initial={[]} onChange={vi.fn()} />);
    expect(screen.getByTestId("template-tasks-empty")).toHaveTextContent("Die Vorlage hat keine Aufgaben.");
  });
});
