import { describe, it, expect, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { EMPTY_PHONE_TICKET_FIELDS } from "./phoneTicketValue";

const { fetchOptions } = vi.hoisted(() => ({ fetchOptions: vi.fn() }));
vi.mock("@/lib/fieldOptionsApi", () => ({ fieldOptionsApi: { fetch: fetchOptions } }));
vi.mock("@/lib/api", () => ({
  api: {
    listReferenceTypes: vi.fn().mockResolvedValue([]),
    listReferenceServices: vi.fn().mockResolvedValue([]),
    listReferenceSlas: vi.fn().mockResolvedValue([]),
  },
}));

import { PhoneTicketFields } from "./PhoneTicketFields";

function wrap(onChange: (v: unknown) => void, responsibleId: number | null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PhoneTicketFields
          value={{ ...EMPTY_PHONE_TICKET_FIELDS, responsibleId }}
          onChange={onChange}
          fields={[]}
          queueId={2}
        />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("PhoneTicketFields responsible", () => {
  it("clears a responsible the queue's scoped list does not offer", async () => {
    fetchOptions.mockReset().mockResolvedValue({ options: { responsible: [{ value: "5", label: "Agent One" }] } });
    const onChange = vi.fn();
    wrap(onChange, 7);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ responsibleId: null })));
  });

  it("keeps a responsible the list offers", async () => {
    fetchOptions.mockReset().mockResolvedValue({ options: { responsible: [{ value: "5", label: "Agent One" }] } });
    const onChange = vi.fn();
    wrap(onChange, 5);
    await waitFor(() => expect(fetchOptions).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 30));
    expect(onChange).not.toHaveBeenCalled();
  });
});
