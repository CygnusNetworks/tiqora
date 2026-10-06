import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { DataTable } from "./DataTable";

describe("DataTable column priority", () => {
  it("hides low-priority columns below their breakpoint without a conflicting display class", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <DataTable
          columns={[
            { key: "name", header: "Name", render: (r: { id: number }) => `row ${r.id}` },
            { key: "changed", header: "Changed", hideBelow: "xl", render: () => "today" },
          ]}
          rows={[{ id: 1 }]}
          rowKey={(r) => r.id}
        />
      </I18nextProvider>,
    );
    const header = screen.getByText("Changed").closest("th");
    expect(header?.className).toContain("hidden xl:table-cell");

    const hiddenCell = screen.getByText("today").closest("td");
    expect(hiddenCell?.className).toContain("md:hidden xl:table-cell");
    expect(hiddenCell?.className).not.toContain("md:table-cell");

    const plainCell = screen.getByText("row 1").closest("td");
    expect(plainCell?.className).toContain("md:table-cell");
    expect(plainCell?.className).not.toContain("md:hidden");
  });
});
