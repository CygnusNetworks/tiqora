import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CrudDrawer, type FieldDef } from "./CrudDrawer";

function wrap(fields: FieldDef[]) {
  return render(
    <I18nextProvider i18n={i18n}>
      <CrudDrawer
        open
        onClose={vi.fn()}
        title="Edit"
        fields={fields}
        initialValues={{ text: "Hello prose" }}
        mode="edit"
        onSubmit={async () => undefined}
        testIdPrefix="admin-form"
      />
    </I18nextProvider>,
  );
}

describe("CrudDrawer font for prose fields", () => {
  it("uses proportional font-sans for signature/template body textareas", () => {
    wrap([
      {
        name: "text",
        label: "Text",
        type: "textarea",
        mono: false,
        rows: 10,
      },
    ]);
    const ta = screen.getByTestId("admin-form-text");
    expect(ta.className).toContain("font-sans");
    expect(ta.className).not.toContain("font-mono");
  });

  it("applies font-mono only when mono is opted in", () => {
    wrap([
      {
        name: "text",
        label: "Code",
        type: "textarea",
        mono: true,
      },
    ]);
    const ta = screen.getByTestId("admin-form-text");
    expect(ta.className).toContain("font-mono");
    expect(ta.className).not.toContain("font-sans");
  });
});
