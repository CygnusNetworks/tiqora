import type { ComponentProps } from "react";
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

describe("CrudDrawer tab accessibility", () => {
  it("links tabs and panels and moves with arrow keys", () => {
    wrap(TABBED, { initialValues: {} });
    const [account, person] = screen.getAllByRole("tab");
    expect(account).toHaveAttribute("aria-selected", "true");
    expect(account).toHaveAttribute("tabindex", "0");
    expect(person).toHaveAttribute("tabindex", "-1");
    const panel = screen.getByRole("tabpanel");
    expect(account.getAttribute("aria-controls")).toBe(panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", account.id);

    fireEvent.keyDown(account, { key: "ArrowRight" });
    expect(person).toHaveAttribute("aria-selected", "true");
    expect(person).toHaveFocus();
    fireEvent.keyDown(person, { key: "ArrowRight" });
    expect(account).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(account, { key: "End" });
    expect(person).toHaveAttribute("aria-selected", "true");
  });
});

function wrapWith(
  fields: FieldDef[],
  props: Partial<ComponentProps<typeof CrudDrawer>>,
  initialValues: Record<string, unknown> = {},
  onSubmit: (v: Record<string, unknown>) => Promise<void> = async () => undefined,
) {
  return render(
    <I18nextProvider i18n={i18n}>
      <CrudDrawer
        open
        onClose={vi.fn()}
        title="Edit"
        fields={fields}
        initialValues={initialValues}
        mode="edit"
        onSubmit={onSubmit}
        testIdPrefix="admin-form"
        {...props}
      />
    </I18nextProvider>,
  );
}

describe("CrudDrawer stableTabHeight", () => {
  it("keeps every tab mounted, only the active one exposed", () => {
    wrapWith(TABBED, { stableTabHeight: true });
    // Both tabs' fields exist (one shared grid cell sizes the body)…
    expect(screen.getByTestId("admin-form-login")).toBeInTheDocument();
    expect(screen.getByTestId("admin-form-first_name")).toBeInTheDocument();
    // …but only the active panel is exposed to assistive tech.
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
    expect(screen.getByTestId("admin-form-tabpanel-1")).toHaveAttribute("aria-hidden", "true");
    fireEvent.click(screen.getByRole("tab", { name: /Person/ }));
    expect(screen.getByTestId("admin-form-tabpanel-0")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByTestId("admin-form-tabpanel-1")).not.toHaveAttribute("aria-hidden");
  });
});

describe("CrudDrawer settings appearance", () => {
  const FIELDS: FieldDef[] = [
    { name: "intro", label: "", type: "section", helpText: "What this is about." },
    { name: "name", label: "Display name", type: "text", required: true },
    { name: "timeout", label: "Timeout", type: "number", unit: "min" },
    {
      name: "valid_id",
      label: "Status",
      type: "switch",
      switchValues: { on: 1, off: 2 },
      switchLabels: { on: "Valid", off: "Invalid" },
    },
    {
      name: "mode",
      label: "Mode",
      type: "segmented",
      required: true,
      options: [
        { value: "a", label: "Alpha" },
        { value: "b", label: "Beta" },
        { value: "c", label: "Gamma" },
      ],
    },
  ];

  it("uses sentence-case labels; the default appearance keeps uppercase", () => {
    const { unmount } = wrapWith(
      FIELDS,
      { appearance: "settings" },
      { name: "x", valid_id: 1, mode: "a" },
    );
    expect(document.getElementById("admin-form-name-label")?.className).not.toContain("uppercase");
    expect(screen.getByText("What this is about.")).toBeInTheDocument();
    expect(screen.getByText("min")).toBeInTheDocument();
    unmount();
    wrapWith([{ name: "name", label: "Display name", type: "text" }], {});
    expect(document.getElementById("admin-form-name-label")?.className).toContain("uppercase");
  });

  it("maps switch values and drives the segmented radiogroup with arrows", async () => {
    const onSubmit = vi.fn(async (_v: Record<string, unknown>) => undefined);
    wrapWith(FIELDS, { appearance: "settings" }, { name: "x", valid_id: 1, mode: "a" }, onSubmit);

    const sw = screen.getByRole("switch", { name: "Status" });
    expect(sw).toBeChecked();
    expect(screen.getByText("Valid")).toBeInTheDocument();
    fireEvent.click(sw);
    expect(screen.getByText("Invalid")).toBeInTheDocument();

    const group = screen.getByRole("radiogroup", { name: /Mode/ });
    const alpha = screen.getByRole("radio", { name: "Alpha" });
    expect(group).toContainElement(alpha);
    expect(alpha).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: "Beta" })).toHaveAttribute("tabindex", "-1");
    fireEvent.keyDown(alpha, { key: "ArrowLeft" });
    expect(screen.getByRole("radio", { name: "Gamma" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Gamma" })).toHaveFocus();

    fireEvent.click(screen.getByTestId("admin-form-submit"));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0][0]).toEqual({ name: "x", valid_id: 2, mode: "c" });
  });

  it("flags an empty required segmented field and focuses its first option", async () => {
    const onSubmit = vi.fn(async () => undefined);
    wrapWith(FIELDS, { appearance: "settings" }, { name: "x", valid_id: 1 }, onSubmit);
    fireEvent.click(screen.getByTestId("admin-form-submit"));
    await waitFor(() =>
      expect(screen.getByRole("radiogroup", { name: /Mode/ })).toHaveAttribute(
        "aria-invalid",
        "true",
      ),
    );
    expect(screen.getByRole("radio", { name: "Alpha" })).toHaveFocus();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
