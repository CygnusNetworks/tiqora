import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { CustomerAiPause } from "./CustomerAiPause";

const { get, set, lift } = vi.hoisted(() => ({ get: vi.fn(), set: vi.fn(), lift: vi.fn() }));

vi.mock("@/lib/customerAiPauseApi", () => ({ customerAiPauseApi: { get, set, lift } }));

function wrap(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

const off = { active: false, paused_until: null, reason: null, by_name: null, since: null };

describe("CustomerAiPause", () => {
  beforeEach(() => {
    get.mockReset();
    set.mockReset();
    lift.mockReset();
  });

  it("switches the automation off for the chosen number of days", async () => {
    get.mockResolvedValue(off);
    set.mockResolvedValue({ ...off, active: true });
    wrap(<CustomerAiPause scope="user" customerKey="anna" canEdit />);

    fireEvent.change(await screen.findByTestId("customer-ai-pause-days"), {
      target: { value: "30" },
    });
    fireEvent.change(screen.getByTestId("customer-ai-pause-reason"), {
      target: { value: "Streit" },
    });
    fireEvent.click(screen.getByTestId("customer-ai-pause-set"));
    await waitFor(() =>
      expect(set).toHaveBeenCalledWith("user", "anna", { days: 30, reason: "Streit" }),
    );
  });

  it("lifts an active, indefinite pause", async () => {
    get.mockResolvedValue({ ...off, active: true, by_name: "Erika", reason: "VIP" });
    lift.mockResolvedValue(undefined);
    wrap(<CustomerAiPause scope="company" customerKey="ACME" canEdit />);

    expect(await screen.findByTestId("customer-ai-pause-state")).toHaveTextContent(
      "Switched off until lifted.",
    );
    fireEvent.click(screen.getByTestId("customer-ai-pause-lift"));
    await waitFor(() => expect(lift).toHaveBeenCalledWith("company", "ACME"));
  });

  it("is hidden for agents who may not edit while nothing is paused", async () => {
    get.mockResolvedValue(off);
    const { container } = wrap(
      <CustomerAiPause scope="user" customerKey="anna" canEdit={false} />,
    );
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
