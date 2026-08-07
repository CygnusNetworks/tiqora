import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CrudDrawer, type FieldDef } from "./CrudDrawer";

function wrap(
  fields: FieldDef[],
  opts: { initialValues?: Record<string, unknown>; onSubmit?: () => Promise<void> } = {},
) {
  return render(
    <I18nextProvider i18n={i18n}>
      <CrudDrawer
        open
        onClose={vi.fn()}
        title="Edit"
        fields={fields}
        initialValues={opts.initialValues ?? { text: "Hello prose" }}
        mode="edit"
        onSubmit={opts.onSubmit ?? (async () => undefined)}
        testIdPrefix="admin-form"
      />
    </I18nextProvider>,
  );
}

const TABBED: FieldDef[] = [
  { name: "login", label: "Login", type: "text", required: true, tab: "Account" },
  { name: "first_name", label: "First name", type: "text", required: true, tab: "Person" },
  { name: "last_name", label: "Last name", type: "text", tab: "Person" },
];

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

describe("CrudDrawer tabs", () => {
  it("renders no tab bar when no field declares a tab", () => {
    wrap([{ name: "text", label: "Text", type: "text" }]);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("shows only the active tab's fields and switches on click", () => {
    wrap(TABBED, { initialValues: {} });
    expect(screen.getByTestId("admin-form-login")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-form-first_name")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Person/ }));
    expect(screen.getByTestId("admin-form-first_name")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-form-login")).not.toBeInTheDocument();
  });

  it("counts required-but-empty fields on the tab that is not open", () => {
    wrap(TABBED, { initialValues: {} });
    // `first_name` is required and empty, and lives on the inactive tab.
    expect(screen.getByRole("tab", { name: /Person/ })).toHaveTextContent("1");
    fireEvent.click(screen.getByRole("tab", { name: /Person/ }));
    fireEvent.change(screen.getByTestId("admin-form-first_name"), {
      target: { value: "Bob" },
    });
    expect(screen.getByRole("tab", { name: /Person/ })).not.toHaveTextContent("1");
  });

  it("switches to the offending tab instead of submitting silently", async () => {
    const onSubmit = vi.fn(async () => undefined);
    wrap(TABBED, { initialValues: { login: "bob" }, onSubmit });

    fireEvent.click(screen.getByTestId("admin-form-submit"));

    // `first_name` is required, empty, and on the other tab — the drawer must
    // reveal it rather than save an incomplete record.
    await waitFor(() => expect(screen.getByTestId("admin-form-first_name")).toBeInTheDocument());
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe("CrudDrawer field help popover", () => {
  it("renders no help trigger when the field has no help", () => {
    wrap([{ name: "text", label: "Text", type: "text" }]);
    expect(screen.queryByTestId("admin-form-text-help")).not.toBeInTheDocument();
  });

  it("opens the popover and shows the description when a field defines help", () => {
    wrap([
      {
        name: "text",
        label: "Text",
        type: "text",
        help: { title: "Text", description: "Explains what this field does." },
      },
    ]);
    const trigger = screen.getByTestId("admin-form-text-help");
    expect(screen.queryByTestId("admin-form-text-help-panel")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.getByTestId("admin-form-text-help-panel")).toHaveTextContent(
      "Explains what this field does.",
    );
  });
});
