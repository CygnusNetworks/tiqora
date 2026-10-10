import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { api } from "@/lib/api";
import { fieldOptionsApi } from "@/lib/fieldOptionsApi";
import type { TicketFilter } from "@/lib/savedSearchesApi";
import { FilterBuilder } from "./FilterBuilder";
import type { FilterFieldDef } from "./filterModel";

vi.mock("@/lib/api", () => ({ api: { request: vi.fn() } }));
vi.mock("@/lib/fieldOptionsApi", () => ({ fieldOptionsApi: { fetch: vi.fn() } }));

const f = (over: Partial<FilterFieldDef> & Pick<FilterFieldDef, "key" | "label" | "type" | "ops">): FilterFieldDef => ({
  enumValues: [],
  optionsSource: null,
  ...over,
});

const FIELDS: FilterFieldDef[] = [
  f({ key: "queue_id", label: "Queue", type: "id", ops: ["eq", "in", "neq", "not_in"], optionsSource: "queue" }),
  f({ key: "title", label: "Titel", type: "string", ops: ["contains", "eq", "in", "is_empty", "starts_with"] }),
  f({ key: "locked", label: "Gesperrt", type: "bool", ops: ["is_false", "is_true"] }),
  f({ key: "created", label: "Erstellt", type: "datetime", ops: ["after", "before", "between", "within_last"] }),
  f({
    key: "channel",
    label: "Kanal",
    type: "enum",
    ops: ["eq", "in"],
    enumValues: [
      { value: "email", label: "E-Mail" },
      { value: "phone", label: "Telefon" },
    ],
  }),
  f({ key: "df.Area", label: "Bereich", type: "string", ops: ["eq", "in"], optionsSource: "df:Area" }),
];

const DEEP = { version: 1, where: { all: [{ any: [{ all: [{ field: "tn", op: "eq", value: "1" }] }] }] } };

// No user-event in this repo: thin helpers over fireEvent.
const user = {
  click: async (el: Element) => void fireEvent.click(el),
  type: async (el: Element, text: string) => {
    for (const ch of text) fireEvent.change(el, { target: { value: (el as HTMLInputElement).value + ch } });
  },
};

function renderBuilder(value: TicketFilter | null, onChange = vi.fn(), extra: Partial<Parameters<typeof FilterBuilder>[0]> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (v: TicketFilter | null) => (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <FilterBuilder fields={FIELDS} value={v} onChange={onChange} {...extra} />
      </QueryClientProvider>
    </I18nextProvider>
  );
  const utils = render(ui(value));
  return { onChange, rerenderWith: (v: TicketFilter | null) => utils.rerender(ui(v)) };
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

beforeEach(() => {
  vi.mocked(api.request).mockReset();
  vi.mocked(api.request).mockImplementation(async (_m: string, path: string) => {
    if (path.endsWith("/reference/queues")) return [{ id: 1, name: "Support" }, { id: 2, name: "Vertrieb" }];
    return [];
  });
  vi.mocked(fieldOptionsApi.fetch).mockReset();
  vi.mocked(fieldOptionsApi.fetch).mockResolvedValue({ options: { DynamicField_Area: [{ value: "nord", label: "Nord" }, { value: "sued", label: "Süd" }] } });
});

describe("FilterBuilder", () => {
  it("adds a condition, limits operators to the field and emits a filter", async () => {
    const { onChange } = renderBuilder(null);
    await user.click(screen.getByTestId("filter-builder-add-condition"));
    await user.click(screen.getByTestId("filter-builder-row-0-field"));
    await user.click(screen.getByRole("option", { name: "Titel" }));
    const ops = within(screen.getByTestId("filter-builder-row-0-op")).queryAllByRole("option");
    expect(ops.map((o) => o.textContent)).not.toContain("zwischen");
    expect(ops.map((o) => o.textContent)).toContain("enthält");
    await user.type(screen.getByTestId("filter-builder-row-0-value"), "VPN");
    expect(onChange).toHaveBeenLastCalledWith({ version: 1, where: { all: [{ field: "title", op: "eq", value: "VPN" }] } });
  });

  it("switching the field resets operator and value", async () => {
    const { onChange } = renderBuilder({ version: 1, where: { all: [{ field: "queue_id", op: "in", value: [1] }] } } as never);
    await user.click(screen.getByTestId("filter-builder-row-0-field"));
    await user.click(screen.getByRole("option", { name: "Titel" }));
    expect(onChange).toHaveBeenLastCalledWith({ version: 1, where: { all: [{ field: "title", op: "eq", value: "" }] } });
  });

  it("starts a new bool condition on is_true without a value", async () => {
    const { onChange } = renderBuilder(null);
    await user.click(screen.getByTestId("filter-builder-add-condition"));
    await user.click(screen.getByTestId("filter-builder-row-0-field"));
    await user.click(screen.getByRole("option", { name: "Gesperrt" }));
    expect(onChange).toHaveBeenLastCalledWith({ version: 1, where: { all: [{ field: "locked", op: "is_true" }] } });
    expect(screen.queryByTestId("filter-builder-row-0-value")).toBeNull();
  });

  it("shows a too-complex filter read-only and can reset it", async () => {
    const { onChange } = renderBuilder(DEEP as never);
    expect(screen.getByTestId("filter-builder-too-complex")).toHaveTextContent("zu komplex");
    expect(screen.queryByTestId("filter-builder-add-condition")).toBeNull();
    await user.click(screen.getByTestId("filter-builder-reset"));
    expect(onChange).toHaveBeenLastCalledWith({ version: 1 });
  });

  it("toggles all/any on the root", async () => {
    const { onChange } = renderBuilder({
      version: 1,
      where: { all: [{ field: "title", op: "contains", value: "VPN" }, { field: "locked", op: "is_true" }] },
    } as never);
    await user.click(screen.getByTestId("filter-builder-mode-any"));
    expect(onChange).toHaveBeenLastCalledWith({
      version: 1,
      where: { any: [{ field: "title", op: "contains", value: "VPN" }, { field: "locked", op: "is_true" }] },
    });
  });

  it("removes a row with the button", async () => {
    const { onChange } = renderBuilder({
      version: 1,
      where: { all: [{ field: "title", op: "contains", value: "VPN" }, { field: "locked", op: "is_true" }] },
    } as never);
    await user.click(screen.getByTestId("filter-builder-row-0-remove"));
    expect(onChange).toHaveBeenLastCalledWith({ version: 1, where: { all: [{ field: "locked", op: "is_true" }] } });
    expect(screen.queryByTestId("filter-builder-row-1-field")).toBeNull();
  });

  it("adds a nested group with its own rows and negation", async () => {
    const { onChange } = renderBuilder(null);
    await user.click(screen.getByTestId("filter-builder-add-group"));
    expect(screen.getByTestId("filter-builder-group-0-row-0-field")).toBeInTheDocument();
    expect(screen.queryByTestId("filter-builder-group-0-add-group")).toBeNull();
    await user.click(screen.getByTestId("filter-builder-group-0-not"));
    const last = onChange.mock.lastCall?.[0] as TicketFilter;
    expect(last.where).toEqual({ all: [{ not: { any: [{ field: "queue_id", op: "eq", value: "" }] } }] });
    await user.click(screen.getByTestId("filter-builder-group-0-remove"));
    expect(onChange).toHaveBeenLastCalledWith({ version: 1 });
  });

  it("offers loaded options for id fields and stores numeric ids", async () => {
    const { onChange } = renderBuilder({ version: 1, where: { all: [{ field: "queue_id", op: "in", value: [] }] } } as never);
    await user.click(await screen.findByTestId("filter-builder-row-0-value-add"));
    await user.click(await screen.findByRole("option", { name: "Vertrieb" }));
    expect(onChange).toHaveBeenLastCalledWith({ version: 1, where: { all: [{ field: "queue_id", op: "in", value: [2] }] } });
    expect(await screen.findByTestId("filter-builder-row-0-value-chip-2")).toHaveTextContent("Vertrieb");
  });

  it("loads dynamic-field options through field-options", async () => {
    const { onChange } = renderBuilder({ version: 1, where: { all: [{ field: "df.Area", op: "eq", value: "" }] } } as never);
    await waitFor(() => expect(fieldOptionsApi.fetch).toHaveBeenCalledWith({ fields: ["DynamicField_Area"] }, expect.anything()));
    await user.click(screen.getByTestId("filter-builder-row-0-value"));
    await user.click(await screen.findByRole("option", { name: "Süd" }));
    expect(onChange).toHaveBeenLastCalledWith({ version: 1, where: { all: [{ field: "df.Area", op: "eq", value: "sued" }] } });
  });

  it("edits date ranges and relative spans, and shows what is wrong", async () => {
    const { onChange } = renderBuilder({ version: 1, where: { all: [{ field: "created", op: "between", value: { from: "", to: "" } }] } } as never);
    fireEvent.change(screen.getByTestId("filter-builder-row-0-value-from"), { target: { value: "2026-01-02" } });
    expect(onChange).toHaveBeenLastCalledWith({
      version: 1,
      where: { all: [{ field: "created", op: "between", value: { from: "2026-01-02", to: "" } }] },
    });
    expect(screen.getByTestId("filter-builder-row-0-error")).toHaveTextContent("gültiges Datum");
    expect(screen.getByTestId("filter-builder-row-0-value-from")).toHaveAttribute("aria-invalid", "true");

    fireEvent.change(screen.getByTestId("filter-builder-row-0-op"), { target: { value: "within_last" } });
    expect(onChange).toHaveBeenLastCalledWith({
      version: 1,
      where: { all: [{ field: "created", op: "within_last", value: { amount: 1, unit: "day" } }] },
    });
    fireEvent.change(screen.getByTestId("filter-builder-row-0-value-unit"), { target: { value: "week" } });
    expect(onChange).toHaveBeenLastCalledWith({
      version: 1,
      where: { all: [{ field: "created", op: "within_last", value: { amount: 1, unit: "week" } }] },
    });
  });

  it("takes over a different filter from the parent", () => {
    const { rerenderWith } = renderBuilder({ version: 1, where: { all: [{ field: "title", op: "eq", value: "a" }] } } as never);
    expect(screen.getByTestId("filter-builder-row-0-value")).toHaveValue("a");
    rerenderWith({ version: 1, where: { all: [{ field: "title", op: "eq", value: "b" }] } } as never);
    expect(screen.getByTestId("filter-builder-row-0-value")).toHaveValue("b");
  });

  it("names inputs and disables everything when disabled", () => {
    renderBuilder({ version: 1, where: { all: [{ field: "title", op: "eq", value: "a" }] } } as never, vi.fn(), { disabled: true });
    expect(screen.getByLabelText("Titel: Wert")).toBeDisabled();
    expect(screen.getByLabelText("Titel: Vergleich")).toBeDisabled();
    expect(screen.getByTestId("filter-builder-add-condition")).toBeDisabled();
    expect(screen.getByTestId("filter-builder-row-0-remove")).toBeDisabled();
  });
});
