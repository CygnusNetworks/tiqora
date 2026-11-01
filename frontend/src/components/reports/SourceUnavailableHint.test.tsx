import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SourceUnavailableHint } from "./SourceUnavailableHint";

const status = vi.hoisted(() => vi.fn());
vi.mock("@/lib/changeApi", async (original) => ({
  ...(await original<typeof import("@/lib/changeApi")>()),
  changeApi: { status },
}));

function show(source: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SourceUnavailableHint testId="hint" source={source} />
    </QueryClientProvider>,
  );
}

describe("SourceUnavailableHint", () => {
  it("names the switch for a change source while the module is off", async () => {
    status.mockResolvedValue({ reason: "change_off" });
    show("changes");
    await waitFor(() => expect(screen.getByTestId("hint")).toHaveTextContent("reports.builder.sourceSwitchedOff"));
  });

  it("keeps the Mode B reason for the other sources", async () => {
    status.mockResolvedValue({ reason: "change_off" });
    show("cis");
    await waitFor(() => expect(status).toHaveBeenCalled());
    expect(screen.getByTestId("hint")).toHaveTextContent("reports.builder.sourceUnavailable");
  });
});
