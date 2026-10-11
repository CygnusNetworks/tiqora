import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import type { FieldValues } from "@/components/fields";
import type { ProcessDialogFormData } from "@/lib/processApi";
import { useProcessFormOptions, type ProcessOptionsTarget } from "./useProcessFormOptions";

const { startOptions, dialogOptions, portalStartOptions } = vi.hoisted(() => ({
  startOptions: vi.fn(),
  dialogOptions: vi.fn(),
  portalStartOptions: vi.fn(),
}));

vi.mock("@/lib/processApi", async (orig) => {
  const mod = await orig<typeof import("@/lib/processApi")>();
  return { ...mod, processApi: { ...mod.processApi, startFormOptions: startOptions, dialogFormOptions: dialogOptions } };
});
vi.mock("@/lib/portalProcessApi", () => ({
  portalProcessApi: { startFormOptions: portalStartOptions, dialogFormOptions: vi.fn() },
}));

const base = { description_short: "", description_long: "", default: null, options: [], time_units: "0" as const, uploads: false };
const form: ProcessDialogFormData = {
  process_entity_id: "Process-wf3",
  activity_dialog_entity_id: "AD-wf3",
  name: "wf3",
  description_short: "",
  description_long: "",
  submit_button_text: "",
  submit_advice_text: "",
  fields: [
    { ...base, name: "Queue", kind: "select", label: "Queue", required: false, options: [{ value: "1", label: "Raw" }] },
    { ...base, name: "Owner", kind: "select", label: "Owner", required: false, refresh_on: ["Queue"] },
    { ...base, name: "Title", kind: "text", label: "Title", required: false },
  ],
};
const start: ProcessOptionsTarget = { scope: "agent", kind: "start", processEntityId: "Process-wf3" };
const dialog: ProcessOptionsTarget = { scope: "agent", kind: "dialog", ticketId: 7, dialogEntityId: "AD-wf3" };

function setup(target: ProcessOptionsTarget = start) {
  return renderHook(({ values }: { values: FieldValues }) => useProcessFormOptions(target, form, values), {
    initialProps: { values: { Queue: "1", Title: "" } as FieldValues },
  });
}

describe("useProcessFormOptions", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    startOptions.mockReset().mockResolvedValue({ options: { Owner: [{ value: "5", label: "Wf3 Agent" }] } });
    dialogOptions.mockReset().mockResolvedValue({ options: { Owner: [{ value: "6", label: "Wf3 Other" }] } });
    portalStartOptions.mockReset().mockResolvedValue({ options: {} });
  });
  afterEach(() => vi.useRealTimers());

  it("useProcessFormOptions re-asks only after a refresh_on source changes, debounced 300 ms", async () => {
    const { result, rerender } = setup();
    // nothing is asked at open
    await act(async () => void (await vi.advanceTimersByTimeAsync(1000)));
    expect(startOptions).not.toHaveBeenCalled();
    expect(result.current).toEqual({});

    // a field nobody depends on changes: still nothing
    rerender({ values: { Queue: "1", Title: "neu" } });
    await act(async () => void (await vi.advanceTimersByTimeAsync(1000)));
    expect(startOptions).not.toHaveBeenCalled();

    // the source changes: not before 300 ms, then once, for the dependent field only
    rerender({ values: { Queue: "2", Title: "neu" } });
    await act(async () => void (await vi.advanceTimersByTimeAsync(299)));
    expect(startOptions).not.toHaveBeenCalled();
    await act(async () => void (await vi.advanceTimersByTimeAsync(1)));
    expect(startOptions).toHaveBeenCalledTimes(1);
    expect(startOptions.mock.calls[0][0]).toBe("Process-wf3");
    expect(startOptions.mock.calls[0][1]).toEqual({ values: { Queue: "2", Title: "neu" }, fields: ["Owner"] });
    expect(result.current.Owner).toEqual([{ value: "5", label: "Wf3 Agent" }]);

    // typing in the source again is collapsed into one request
    rerender({ values: { Queue: "3", Title: "neu" } });
    await act(async () => void (await vi.advanceTimersByTimeAsync(200)));
    rerender({ values: { Queue: "4", Title: "neu" } });
    await act(async () => void (await vi.advanceTimersByTimeAsync(1000)));
    expect(startOptions).toHaveBeenCalledTimes(2);
    expect(startOptions.mock.calls[1][1].values.Queue).toBe("4");
  });

  it("asks the dialog endpoint of the ticket for a dialog target", async () => {
    const { result, rerender } = setup(dialog);
    rerender({ values: { Queue: "9", Title: "" } });
    await act(async () => void (await vi.advanceTimersByTimeAsync(300)));
    expect(dialogOptions).toHaveBeenCalledWith(7, "AD-wf3", { values: { Queue: "9" }, fields: ["Owner"] });
    expect(result.current.Owner).toEqual([{ value: "6", label: "Wf3 Other" }]);
  });

  it("keeps the form's own options when the request fails", async () => {
    startOptions.mockReset().mockRejectedValue(new Error("boom"));
    const { result, rerender } = setup();
    rerender({ values: { Queue: "2", Title: "" } });
    await act(async () => void (await vi.advanceTimersByTimeAsync(300)));
    expect(startOptions).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({});
  });
});
