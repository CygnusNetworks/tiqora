import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { GenericAgentJobDetailPage } from "./GenericAgentJobDetailPage";

let currentJobName = "close-old-tickets";

vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ jobName: currentJobName }),
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode; [k: string]: unknown }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

const getGenericAgentJob = vi.fn();
const listReferenceQueues = vi.fn();
const listReferenceStates = vi.fn();
const listReferencePriorities = vi.fn();
const listReferenceAgents = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getGenericAgentJob: (...args: unknown[]) => getGenericAgentJob(...args),
    listReferenceQueues: (...args: unknown[]) => listReferenceQueues(...args),
    listReferenceStates: (...args: unknown[]) => listReferenceStates(...args),
    listReferencePriorities: (...args: unknown[]) => listReferencePriorities(...args),
    listReferenceAgents: (...args: unknown[]) => listReferenceAgents(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <GenericAgentJobDetailPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("GenericAgentJobDetailPage", () => {
  beforeEach(() => {
    currentJobName = "close-old-tickets";
    getGenericAgentJob.mockReset();
    listReferenceQueues.mockReset();
    listReferenceStates.mockReset();
    listReferencePriorities.mockReset();
    listReferenceAgents.mockReset();

    getGenericAgentJob.mockResolvedValue({
      job_name: "close-old-tickets",
      settings: {
        Valid: ["1"],
        ScheduleDays: ["1", "2", "3", "4", "5"],
        ScheduleHours: ["2"],
        ScheduleMinutes: ["0"],
        StateIDs: ["1", "2"],
        NewStateID: ["4"],
        DynamicField_Foo: ["bar"],
      },
    });
    listReferenceQueues.mockResolvedValue([{ id: 3, name: "Support" }]);
    listReferenceStates.mockResolvedValue([
      { id: 1, name: "open" },
      { id: 2, name: "pending reminder" },
      { id: 4, name: "closed successful" },
    ]);
    listReferencePriorities.mockResolvedValue([{ id: 1, name: "3 normal" }]);
    listReferenceAgents.mockResolvedValue([{ id: 1, login: "agent1", full_name: "Agent One" }]);
  });

  it("loads the job and resolves criteria/action ids to human-readable names", async () => {
    renderPage();

    await screen.findByTestId("admin-generic-agent-job-detail-page");
    await waitFor(() => {
      expect(screen.getByTestId("generic-agent-job-entry-StateIDs")).toHaveTextContent(
        "open, pending reminder",
      );
    });
    expect(screen.getByTestId("generic-agent-job-entry-NewStateID")).toHaveTextContent(
      "closed successful",
    );
    expect(screen.getByTestId("generic-agent-job-entry-DynamicField_Foo")).toHaveTextContent("bar");
  });

  it("shows the job as active with a resolved schedule", async () => {
    renderPage();

    await screen.findByTestId("admin-generic-agent-job-detail-page");
    await waitFor(() => {
      expect(getGenericAgentJob).toHaveBeenCalledWith("close-old-tickets", expect.anything());
    });
    expect(screen.getByText("close-old-tickets")).toBeInTheDocument();
  });

  it("renders an inactive job without a schedule as manual", async () => {
    currentJobName = "manual-cleanup";
    getGenericAgentJob.mockResolvedValue({
      job_name: "manual-cleanup",
      settings: {
        Valid: ["0"],
        Title: ["foo*"],
      },
    });

    renderPage();

    await screen.findByTestId("admin-generic-agent-job-detail-page");
    await waitFor(() => {
      expect(screen.getByTestId("generic-agent-job-entry-Title")).toHaveTextContent("foo*");
    });
  });
});
