import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { insertTagAtCursor, OTRS_PLACEHOLDERS } from "./otrsPlaceholders";
import { VariableReference } from "./VariableReference";

const listQueueVariables = vi.fn();
const listCustomerFields = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminQueueVariables: {
      list: (...args: unknown[]) => listQueueVariables(...args),
    },
    adminCustomerFields: {
      list: (...args: unknown[]) => listCustomerFields(...args),
    },
  },
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("VariableReference", () => {
  beforeEach(() => {
    listQueueVariables.mockReset();
    listCustomerFields.mockReset();
    listQueueVariables.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 500 });
    listCustomerFields.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 500 });
  });

  it("renders categorised groups when expanded", async () => {
    wrap(<VariableReference onInsert={vi.fn()} defaultOpen />);

    expect(screen.getByTestId("variable-reference-group-ticket")).toBeInTheDocument();
    expect(screen.getByTestId("variable-reference-group-customer")).toBeInTheDocument();
    expect(screen.getByTestId("variable-reference-group-agent")).toBeInTheDocument();
    expect(screen.getByTestId("variable-reference-group-queue")).toBeInTheDocument();

    // Sample tags from each group are visible
    expect(screen.getByText("<OTRS_TICKET_TicketNumber>")).toBeInTheDocument();
    expect(screen.getByText("<OTRS_CUSTOMER_DATA_wpnum>")).toBeInTheDocument();
    expect(screen.getByText("<OTRS_CURRENT_UserFirstname>")).toBeInTheDocument();
    expect(screen.getByText("<OTRS_QUEUE_Name>")).toBeInTheDocument();
  });

  it("starts collapsed and expands on toggle", () => {
    wrap(<VariableReference onInsert={vi.fn()} />);

    expect(screen.queryByTestId("variable-reference-panel")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("variable-reference-toggle"));
    expect(screen.getByTestId("variable-reference-panel")).toBeInTheDocument();
  });

  it("calls onInsert with the tag when a variable is clicked", () => {
    const onInsert = vi.fn();
    wrap(<VariableReference onInsert={onInsert} defaultOpen />);

    const tag = "<OTRS_TICKET_TicketNumber>";
    const button = screen.getByText(tag).closest("button");
    expect(button).toBeTruthy();
    fireEvent.click(button!);
    expect(onInsert).toHaveBeenCalledTimes(1);
    expect(onInsert).toHaveBeenCalledWith(tag);
  });

  it("exposes a maintainable placeholder catalogue with groups", () => {
    const groups = new Set(OTRS_PLACEHOLDERS.map((p) => p.group));
    expect(groups).toEqual(new Set(["ticket", "customer", "agent", "queue"]));
    expect(OTRS_PLACEHOLDERS.every((p) => p.tag.startsWith("<OTRS_") && p.descriptionKey)).toBe(
      true,
    );
  });

  it("shows a configured queue variable from the admin list API", async () => {
    listQueueVariables.mockResolvedValue({
      items: [
        {
          id: 1,
          queue_id: 3,
          name: "Domain",
          value: "stw-bonn.de",
          created: "2026-07-20T00:00:00Z",
          changed: "2026-07-20T00:00:00Z",
        },
        {
          id: 2,
          queue_id: null,
          name: "Domain",
          value: "global.example",
          created: "2026-07-20T00:00:00Z",
          changed: "2026-07-20T00:00:00Z",
        },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });
    listCustomerFields.mockResolvedValue({
      items: [
        {
          id: 5,
          source_table: "customer_user",
          column_name: "custom_col",
          tag_name: "CustomCol",
          label: "Custom column",
          enabled: true,
          created: "2026-07-20T00:00:00Z",
          changed: "2026-07-20T00:00:00Z",
        },
        {
          id: 6,
          source_table: "customer_user",
          column_name: "disabled_col",
          tag_name: "Disabled",
          label: "Off",
          enabled: false,
          created: "2026-07-20T00:00:00Z",
          changed: "2026-07-20T00:00:00Z",
        },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });

    wrap(<VariableReference onInsert={vi.fn()} defaultOpen />);

    await waitFor(() => {
      expect(screen.getByText("<OTRS_QUEUE_Domain>")).toBeInTheDocument();
    });
    // Distinct name only once
    expect(screen.getAllByText("<OTRS_QUEUE_Domain>")).toHaveLength(1);

    await waitFor(() => {
      expect(screen.getByText("<OTRS_CUSTOMER_DATA_CustomCol>")).toBeInTheDocument();
    });
    expect(screen.getByText("Custom column")).toBeInTheDocument();
    expect(screen.queryByText("<OTRS_CUSTOMER_DATA_Disabled>")).not.toBeInTheDocument();
  });

  it("falls back to the static list when admin queries fail", async () => {
    listQueueVariables.mockRejectedValue(new Error("boom"));
    listCustomerFields.mockRejectedValue(new Error("boom"));

    wrap(<VariableReference onInsert={vi.fn()} defaultOpen />);

    await waitFor(() => {
      expect(screen.getByText("<OTRS_QUEUE_Name>")).toBeInTheDocument();
    });
    // No error UI
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("insertTagAtCursor", () => {
  it("inserts at selectionStart/selectionEnd", () => {
    const ta = document.createElement("textarea");
    ta.value = "Hello world";
    document.body.appendChild(ta);
    ta.setSelectionRange(6, 6); // before "world"
    const onChange = vi.fn();

    insertTagAtCursor(ta, "Hello world", "<TAG>", onChange);

    expect(onChange).toHaveBeenCalledWith("Hello <TAG>world");
    document.body.removeChild(ta);
  });

  it("appends when control is null", () => {
    const onChange = vi.fn();
    insertTagAtCursor(null, "base", "<TAG>", onChange);
    expect(onChange).toHaveBeenCalledWith("base<TAG>");
  });
});
