import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { EscalationRuleTester } from "./EscalationRuleTester";

const { testEscalationRules } = vi.hoisted(() => ({ testEscalationRules: vi.fn() }));

vi.mock("@/lib/aiApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/aiApi")>("@/lib/aiApi");
  return { ...actual, aiApi: { ...actual.aiApi, testEscalationRules } };
});

function wrap(rulesJson: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <EscalationRuleTester rulesJson={rulesJson} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("EscalationRuleTester", () => {
  beforeEach(() => {
    testEscalationRules.mockReset();
  });

  it("sends the current rules with tool and sample, and shows a hit", async () => {
    testEscalationRules.mockResolvedValue({
      valid: true,
      error: null,
      hit: { rule_index: 0, tool: "a:b", field: "lock_code", match: "exact", value: "COPYR" },
    });
    wrap('[{"tool":"a:b","match":"exact","values":["COPYR"]}]');

    fireEvent.change(screen.getByTestId("admin-ai-escalation-tester-tool"), {
      target: { value: "a:b" },
    });
    fireEvent.change(screen.getByTestId("admin-ai-escalation-tester-sample"), {
      target: { value: '{"lock_code":"COPYR"}' },
    });
    fireEvent.click(screen.getByTestId("admin-ai-escalation-tester-run"));

    await waitFor(() =>
      expect(testEscalationRules).toHaveBeenCalledWith({
        rules_json: '[{"tool":"a:b","match":"exact","values":["COPYR"]}]',
        tool: "a:b",
        sample_json: '{"lock_code":"COPYR"}',
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-escalation-tester-verdict")).toBeTruthy(),
    );
    expect(screen.getByTestId("admin-ai-escalation-tester-hit").textContent).toContain("COPYR");
  });

  it("shows the no-hit and invalid verdicts", async () => {
    testEscalationRules.mockResolvedValue({ valid: true, error: null, hit: null });
    wrap("[]");

    fireEvent.change(screen.getByTestId("admin-ai-escalation-tester-tool"), {
      target: { value: "x:y" },
    });
    fireEvent.click(screen.getByTestId("admin-ai-escalation-tester-run"));
    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-escalation-tester-verdict")).toBeTruthy(),
    );

    testEscalationRules.mockResolvedValue({ valid: false, error: "Rule 0: bad", hit: null });
    fireEvent.click(screen.getByTestId("admin-ai-escalation-tester-run"));
    await waitFor(() =>
      expect(screen.queryByTestId("admin-ai-escalation-tester-error")?.textContent).toContain(
        "Rule 0: bad",
      ),
    );
  });

  it("disables the run button without a tool name", () => {
    wrap("[]");
    expect(screen.getByTestId("admin-ai-escalation-tester-run")).toBeDisabled();
  });
});
