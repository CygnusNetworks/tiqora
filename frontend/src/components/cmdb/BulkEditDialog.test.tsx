import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { cmdbApi, type CatalogItem, type LinkType } from "@/lib/cmdbApi";
import { BulkEditDialog } from "./BulkEditDialog";

const DEPLOY = [{ id: 2, name: "Produktiv" }, { id: 3, name: "Außer Betrieb" }] as CatalogItem[];
const INCIDENT = [{ id: 11, name: "Betriebsbereit" }, { id: 13, name: "Vorfall" }] as CatalogItem[];
const LINK_TYPES = [
  { name: "DependsOn", pointed: true, offer_ci_ci: true, offer_ci_service: true, offer_ticket_ci: false, source_label: "hängt ab von", target_label: "wird benötigt von" },
  { name: "RelevantTo", pointed: true, offer_ci_ci: false, offer_ci_service: false, offer_ticket_ci: true, source_label: "betrifft", target_label: "betroffen von" },
] as LinkType[];

afterEach(() => vi.restoreAllMocks());

function setup() {
  const start = vi.spyOn(cmdbApi, "startBulk").mockResolvedValue({ job_id: 77, status: "queued" } as never);
  vi.spyOn(cmdbApi, "linkTypes").mockResolvedValue(LINK_TYPES);
  const onStarted = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nextProvider i18n={i18n}>
        <BulkEditDialog open ciIds={[1, 2, 3]} deploymentItems={DEPLOY} incidentItems={INCIDENT} onClose={() => {}} onStarted={onStarted} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { start, onStarted };
}

describe("BulkEditDialog", () => {
  it("cannot start without a change", async () => {
    await i18n.changeLanguage("de");
    setup();
    expect(screen.getByTestId("bulk-submit")).toBeDisabled();
  });

  it("sends exactly the chosen fields", async () => {
    const { start, onStarted } = setup();
    fireEvent.click(screen.getByTestId("bulk-deployment"));
    fireEvent.click(await screen.findByTestId("bulk-deployment-menu-option-3"));
    fireEvent.click(screen.getByTestId("bulk-submit"));
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith(77));
    expect(start).toHaveBeenCalledTimes(1);
    expect(start.mock.calls[0][0]).toEqual({ ci_ids: [1, 2, 3], deployment_state_id: 3 });
  });

  it("sends a link with type, target kind, target and side", async () => {
    const { start } = setup();
    fireEvent.click(screen.getByTestId("bulk-link-switch"));
    fireEvent.click(await screen.findByTestId("bulk-target-kind-service"));
    fireEvent.click(screen.getByTestId("bulk-link-type"));
    fireEvent.click(await screen.findByTestId("bulk-link-type-menu-option-DependsOn"));
    expect(screen.getByTestId("bulk-submit")).toBeDisabled(); // target still missing
    fireEvent.change(screen.getByTestId("bulk-target"), { target: { value: " 12 " } });
    fireEvent.click(screen.getByTestId("bulk-ci-side-target"));
    fireEvent.click(screen.getByTestId("bulk-submit"));
    await waitFor(() => expect(start).toHaveBeenCalled());
    expect(start.mock.calls[0][0]).toEqual({
      ci_ids: [1, 2, 3],
      link: { link_type_key: "DependsOn", target_kind: "service", target_key: "12", ci_side: "target" },
    });
  });

  it("offers only the link types of the chosen target kind", async () => {
    setup();
    fireEvent.click(screen.getByTestId("bulk-link-switch"));
    fireEvent.click(await screen.findByTestId("bulk-target-kind-ticket"));
    fireEvent.click(screen.getByTestId("bulk-link-type"));
    expect(await screen.findByTestId("bulk-link-type-menu-option-RelevantTo")).toBeInTheDocument();
    expect(screen.queryByTestId("bulk-link-type-menu-option-DependsOn")).not.toBeInTheDocument();
  });

  it("shows the error when the job cannot be started", async () => {
    const { start, onStarted } = setup();
    start.mockRejectedValueOnce(new Error("boom"));
    fireEvent.click(screen.getByTestId("bulk-incident"));
    fireEvent.click(await screen.findByTestId("bulk-incident-menu-option-13"));
    fireEvent.click(screen.getByTestId("bulk-submit"));
    expect(await screen.findByTestId("bulk-error")).toBeInTheDocument();
    expect(onStarted).not.toHaveBeenCalled();
  });
});
