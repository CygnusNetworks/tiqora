import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { PriorityMatrix } from "@/lib/itsmApi";
import { PriorityMatrixEditor } from "./PriorityMatrixEditor";

const PRIOS = [
  { id: 1, name: "1 very high" },
  { id: 3, name: "3 normal" },
  { id: 5, name: "5 very low" },
];

const matrix = (editable: boolean): PriorityMatrix => ({
  source: editable ? "tiqora" : "znuny",
  editable,
  enabled: true,
  criticality_values: ["low", "high"],
  impact_values: ["small", "large"],
  cells: [
    { criticality: "low", impact: "small", priority_id: 5 },
    { criticality: "low", impact: "large", priority_id: 3 },
    { criticality: "high", impact: "small", priority_id: 3 },
    { criticality: "high", impact: "large", priority_id: 1 },
  ],
});

function renderEditor(m: PriorityMatrix, onSave = vi.fn()) {
  render(
    <I18nextProvider i18n={i18n}>
      <PriorityMatrixEditor matrix={m} priorities={PRIOS} onSave={onSave} />
    </I18nextProvider>,
  );
  return onSave;
}

describe("PriorityMatrixEditor", () => {
  it("saves every cell with the changed priority", () => {
    const onSave = renderEditor(matrix(true));
    expect(screen.getByTestId("priority-matrix-save")).toBeDisabled();
    fireEvent.click(screen.getByTestId("matrix-cell-0-0"));
    fireEvent.click(screen.getByTestId("matrix-cell-0-0-menu-option-1"));
    fireEvent.click(screen.getByTestId("priority-matrix-save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    const cells = onSave.mock.calls[0][0] as PriorityMatrix["cells"];
    expect(cells).toHaveLength(4);
    expect(cells).toContainEqual({ criticality: "low", impact: "small", priority_id: 1 });
    expect(cells).toContainEqual({ criticality: "high", impact: "large", priority_id: 1 });
  });

  it("is read-only for a Znuny matrix: names, no save, a hint", () => {
    renderEditor(matrix(false));
    expect(screen.getByTestId("matrix-cell-0-0")).toHaveTextContent("5 very low");
    expect(screen.getByTestId("matrix-cell-1-1")).toHaveTextContent("1 very high");
    expect(screen.queryByTestId("priority-matrix-save")).toBeNull();
    expect(screen.getByTestId("priority-matrix-readonly")).toBeInTheDocument();
    expect(screen.getByTestId("priority-matrix")).toHaveAttribute("data-editable", "false");
  });
});
