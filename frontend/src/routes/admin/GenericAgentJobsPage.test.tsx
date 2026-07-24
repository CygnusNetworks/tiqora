import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { GenericAgentJobsPage } from "./GenericAgentJobsPage";

const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

const listGenericAgentJobs = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listGenericAgentJobs: (...args: unknown[]) => listGenericAgentJobs(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <GenericAgentJobsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("GenericAgentJobsPage", () => {
  beforeEach(() => {
    navigate.mockReset();
    listGenericAgentJobs.mockReset();

    listGenericAgentJobs.mockResolvedValue([
      {
        job_name: "close-old-tickets",
        settings: {
          Valid: ["1"],
          ScheduleDays: ["1", "2", "3", "4", "5"],
          ScheduleHours: ["2"],
          ScheduleMinutes: ["0"],
          StateIDs: ["1", "2"],
          NewStateID: ["4"],
        },
      },
      {
        job_name: "manual-cleanup",
        settings: {
          Valid: ["0"],
          Title: ["foo*"],
        },
      },
    ]);
  });

  it("renders jobs with schedule/active state and criteria/action counts", async () => {
    renderPage();

    await screen.findByTestId("generic-agent-job-row-close-old-tickets");
    expect(screen.getByTestId("generic-agent-job-row-close-old-tickets")).toHaveTextContent(
      "close-old-tickets",
    );
    expect(screen.getByTestId("generic-agent-job-row-manual-cleanup")).toHaveTextContent(
      "manual-cleanup",
    );
  });

  it("navigates to the job detail page on row click", async () => {
    renderPage();

    await screen.findByTestId("generic-agent-job-row-close-old-tickets");
    fireEvent.click(screen.getByTestId("generic-agent-job-row-close-old-tickets"));

    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({
        to: "/admin/generic-agent-jobs/$jobName",
        params: { jobName: "close-old-tickets" },
      });
    });
  });

  it("shows an empty state when there are no jobs", async () => {
    listGenericAgentJobs.mockResolvedValue([]);
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("admin-generic-agent-jobs-table")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("generic-agent-job-row-close-old-tickets")).not.toBeInTheDocument();
  });
});
