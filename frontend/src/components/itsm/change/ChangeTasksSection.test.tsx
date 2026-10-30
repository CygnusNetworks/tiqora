import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import i18n from "@/i18n";
import { changeError, makeChange, makeTask, renderWithClient } from "./changeTestSupport";
import { ChangeTasksSection } from "./ChangeTasksSection";

const { tasks, createTask, orderTasks } = vi.hoisted(() => ({ tasks: vi.fn(), createTask: vi.fn(), orderTasks: vi.fn() }));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, tasks, createTask, orderTasks } };
});

const three = [
  makeTask(141, { position: 1, title: "Konfiguration sichern", state: "closed successful", state_type: "closed", finished: true }),
  makeTask(142, { position: 2, title: "Firmware einspielen", planned_start: "2026-10-24T20:00:00Z", planned_end: "2026-10-24T23:00:00Z" }),
  makeTask(143, { position: 3, title: "Rückfall", task_kind: "backout" }),
];

describe("ChangeTasksSection", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    for (const f of [tasks, createTask, orderTasks]) f.mockReset();
  });
  afterEach(cleanup);

  it("lists the tasks in order with a link to each task ticket", async () => {
    tasks.mockResolvedValue(three);
    renderWithClient(<ChangeTasksSection change={makeChange()} />);
    const rows = await screen.findAllByTestId("change-task");
    expect(rows.map((r) => r.getAttribute("data-task-id"))).toEqual(["141", "142", "143"]);
    expect(within(rows[0]).getByRole("link", { name: /Konfiguration sichern/ })).toHaveAttribute("href", "/agent/tickets/141");
    expect(rows[1]).toHaveTextContent("2026-10-24 20:00 – 2026-10-24 23:00");
    expect(rows[2]).toHaveTextContent("Rückfall");
    expect(screen.getByTestId("change-tasks-progress")).toHaveTextContent("1 von 3 erledigt");
  });

  it("moves a task down", async () => {
    tasks.mockResolvedValue(three);
    orderTasks.mockResolvedValue([three[1], three[0], three[2]]);
    renderWithClient(<ChangeTasksSection change={makeChange()} />);
    const rows = await screen.findAllByTestId("change-task");
    // the first cannot move up, the last not down
    expect(within(rows[0]).getByTestId("change-task-up")).toBeDisabled();
    expect(within(rows[2]).getByTestId("change-task-down")).toBeDisabled();
    fireEvent.click(within(rows[0]).getByTestId("change-task-down"));
    await waitFor(() => expect(orderTasks).toHaveBeenCalledWith(130, [142, 141, 143]));
  });

  it("adds a task with its kind", async () => {
    tasks.mockResolvedValue([]);
    createTask.mockResolvedValue(makeTask(150));
    renderWithClient(<ChangeTasksSection change={makeChange()} />);
    expect(await screen.findByTestId("change-tasks-empty")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("change-tasks-add"));
    fireEvent.change(screen.getByTestId("change-task-new-title"), { target: { value: "Monitoring prüfen" } });
    fireEvent.click(screen.getByTestId("change-task-new-kind"));
    fireEvent.click(await screen.findByTestId("change-task-new-kind-menu-option-decision"));
    fireEvent.click(screen.getByTestId("change-task-new-submit"));
    await waitFor(() => expect(createTask).toHaveBeenCalledWith(130, { title: "Monitoring prüfen", task_kind: "decision" }));
  });

  it("names a refused task write", async () => {
    tasks.mockResolvedValue([]);
    createTask.mockRejectedValue(changeError(409, { code: "max_tasks_reached" }));
    renderWithClient(<ChangeTasksSection change={makeChange()} />);
    fireEvent.click(await screen.findByTestId("change-tasks-add"));
    fireEvent.change(screen.getByTestId("change-task-new-title"), { target: { value: "Noch eine" } });
    fireEvent.click(screen.getByTestId("change-task-new-submit"));
    expect(await screen.findByTestId("change-tasks-error")).toHaveTextContent("Der Change hat schon die größte mögliche Zahl an Aufgaben.");
  });

  it("drops an earlier add error once a move succeeds", async () => {
    tasks.mockResolvedValue(three);
    createTask.mockRejectedValue(changeError(409, { code: "max_tasks_reached" }));
    orderTasks.mockResolvedValue(three);
    renderWithClient(<ChangeTasksSection change={makeChange()} />);
    fireEvent.click(await screen.findByTestId("change-tasks-add"));
    fireEvent.change(screen.getByTestId("change-task-new-title"), { target: { value: "Noch eine" } });
    fireEvent.click(screen.getByTestId("change-task-new-submit"));
    await screen.findByTestId("change-tasks-error");
    fireEvent.click(screen.getAllByTestId("change-task-down")[0]);
    await waitFor(() => expect(orderTasks).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByTestId("change-tasks-error")).toBeNull());
  });

  it("is read-only on a final change", async () => {
    tasks.mockResolvedValue(three);
    renderWithClient(<ChangeTasksSection change={makeChange({ phase: "closed" })} />);
    await screen.findAllByTestId("change-task");
    expect(screen.queryByTestId("change-tasks-add")).toBeNull();
    expect(screen.queryByTestId("change-task-up")).toBeNull();
  });
});
