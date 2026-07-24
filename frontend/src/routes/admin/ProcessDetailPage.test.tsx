import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { ProcessDetailPage } from "./ProcessDetailPage";

const getProcess = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ processEntityId: "Process-1" }),
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode; [k: string]: unknown }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", () => ({
  api: {
    getProcess: (...args: unknown[]) => getProcess(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ProcessDetailPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleProcess = {
  id: 1,
  entity_id: "Process-1",
  name: "Onboarding",
  state_entity_id: "ProcessState-1",
  start_activity_entity_id: "Activity-1",
  activities: [
    {
      entity_id: "Activity-1",
      name: "Welcome",
      activity_dialogs: [{ entity_id: "ActivityDialog-1", name: "Welcome Dialog" }],
    },
    {
      entity_id: "Activity-2",
      name: "Closing",
      activity_dialogs: [],
    },
  ],
};

describe("ProcessDetailPage", () => {
  beforeEach(() => {
    getProcess.mockReset();
    getProcess.mockResolvedValue(sampleProcess);
  });

  it("fetches the process and renders its activities and dialogs", async () => {
    renderPage();

    await waitFor(() => {
      expect(getProcess).toHaveBeenCalledWith("Process-1", expect.anything());
    });
    expect(await screen.findByText("Onboarding")).toBeInTheDocument();
    expect(screen.getByTestId("process-activity-Activity-1")).toHaveTextContent("Welcome");
    expect(screen.getByTestId("process-activity-Activity-1")).toHaveTextContent("Start activity");
    expect(screen.getByText("Welcome Dialog")).toBeInTheDocument();
  });

  it("shows a placeholder for activities without dialogs", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("process-activity-Activity-2")).toBeInTheDocument();
    });
    expect(screen.getByTestId("process-activity-Activity-2")).not.toHaveTextContent("Start activity");
  });

  it("shows an error message when the process fails to load", async () => {
    getProcess.mockRejectedValue(new Error("boom"));
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Could not load process.")).toBeInTheDocument();
    });
  });
});
