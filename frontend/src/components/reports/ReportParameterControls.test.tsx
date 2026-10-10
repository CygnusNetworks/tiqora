import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { demoReport, demoSources } from "@/demo/reportsMock";
import type { ReportDefinition, ReportParameter } from "@/lib/reportsApi";
import { ReportParameterControls } from "./ReportParameterControls";

const P = (p: Partial<ReportParameter> & Pick<ReportParameter, "key" | "field">): ReportParameter => ({
  op: "in",
  required: false,
  ...p,
});

const SOURCE = demoSources().find((s) => s.key === "tickets")!;

function defWith(parameters: ReportParameter[]): ReportDefinition {
  return { ...demoReport("builtin.volume")!.definition, source: "tickets", parameters };
}

function Providers({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
    </QueryClientProvider>
  );
}

function renderControls(parameters: ReportParameter[], values = {}, onChange = vi.fn()) {
  render(
    <Providers>
      <ReportParameterControls definition={defWith(parameters)} source={SOURCE} values={values} onChange={onChange} testId="p" />
    </Providers>,
  );
  return onChange;
}

const channel = (over: Partial<ReportParameter> = {}): ReportParameter => P({ key: "ch", field: "channel", ...over });

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

describe("ReportParameterControls", () => {
  it("limits choices to allowed_values and says a required value is missing", () => {
    renderControls([channel({ allowed_values: ["email", "phone"], required: true })]);
    fireEvent.click(screen.getByTestId("p-ch"));
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["E-Mail", "Telefon"]);
    expect(screen.getByTestId("p-ch-required")).toHaveTextContent("Bitte Kanal wählen");
  });

  it("adds a choice to the list and removes a chip", () => {
    const onChange = renderControls([channel()], { ch: ["email"] });
    expect(screen.queryByTestId("p-ch-required")).toBeNull();
    fireEvent.click(screen.getByTestId("p-ch"));
    fireEvent.click(screen.getByRole("option", { name: "Telefon" }));
    expect(onChange).toHaveBeenLastCalledWith({ ch: ["email", "phone"] });
    fireEvent.click(screen.getByRole("button", { name: /E-Mail/ }));
    expect(onChange).toHaveBeenLastCalledWith({ ch: [] });
  });

  it("is a single select for eq: one value replaces the other", () => {
    const onChange = renderControls([channel({ op: "eq", required: true })], { ch: ["email"] });
    expect(screen.getByTestId("p-ch")).toHaveTextContent("E-Mail");
    expect(screen.queryByTestId("p-ch-required")).toBeNull();
    fireEvent.click(screen.getByTestId("p-ch"));
    fireEvent.click(screen.getByRole("option", { name: "Telefon" }));
    expect(onChange).toHaveBeenLastCalledWith({ ch: ["phone"] });
  });

  it("switches a boolean parameter", () => {
    const onChange = renderControls([P({ key: "arch", field: "archived", op: "is_true" })]);
    const sw = screen.getByRole("switch");
    expect(sw).not.toBeChecked();
    fireEvent.click(sw);
    expect(onChange).toHaveBeenLastCalledWith({ arch: true });
  });

  it("leaves the queue parameter to the filter bar", () => {
    renderControls([P({ key: "queues", field: "queue", op: "in" }), channel()]);
    expect(screen.queryByTestId("p-queues")).toBeNull();
    const group = screen.getByTestId("p-ch-field");
    expect(within(group).getByText("Kanal")).toBeInTheDocument();
  });

  it("renders nothing without runtime parameters", () => {
    renderControls([P({ key: "queues", field: "queue", op: "in" })]);
    expect(screen.queryByTestId("p")).toBeNull();
  });
});
