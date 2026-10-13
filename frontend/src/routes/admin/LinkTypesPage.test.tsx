import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { api, ApiError } from "@/lib/api";
import type { ItsmAdmin, LinkType } from "@/lib/itsmApi";
import { LinkTypesPage } from "./LinkTypesPage";

const row = (over: Partial<LinkType>): LinkType => ({
  id: 1,
  name: "Normal",
  origin: "znuny",
  source_label: "Normal",
  target_label: "Normal",
  pointed: false,
  propagation: "none",
  offer_ticket_ticket: true,
  offer_ticket_ci: false,
  offer_ci_ci: true,
  offer_ci_service: false,
  position: 0,
  valid: true,
  znuny_source_name: "Normal",
  znuny_target_name: "Normal",
  ...over,
});

const ROWS = [
  row({}),
  row({ id: 2, name: "Affects", origin: "tiqora", source_label: "Affects", target_label: "Affected by", propagation: "source", pointed: true }),
];

let mode: ItsmAdmin["mode"];
let request: ReturnType<typeof vi.spyOn>;

async function reads(method: string, path: string): Promise<unknown> {
  if (path === "/api/v1/admin/itsm/link-types" && method === "GET") return { items: ROWS, missing_from_table: ["Depends"] };
  if (path === "/api/v1/admin/itsm" && method === "GET") return { mode };
  throw new Error(`unexpected ${method} ${path}`);
}

beforeEach(() => {
  mode = "tiqora";
  request = vi.spyOn(api, "request").mockImplementation(reads as never);
});
afterEach(() => vi.restoreAllMocks());

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <LinkTypesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("LinkTypesPage", () => {
  it("lists the types with their origin and the Znuny types still without a row", async () => {
    renderPage();
    expect(await screen.findByTestId("link-type-origin-Normal")).toHaveTextContent(i18n.t("admin.itsm.linkTypes.origins.znuny"));
    expect(screen.getByTestId("link-type-origin-Affects")).toHaveTextContent(i18n.t("admin.itsm.linkTypes.origins.tiqora"));
    expect(screen.getByTestId("link-types-missing")).toHaveTextContent("Depends");
  });

  it("patches only the changed keys of a Znuny type and offers no propagation for it", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("link-type-edit-Normal"));
    expect(screen.queryByTestId("link-type-propagation")).toBeNull();
    fireEvent.change(screen.getByTestId("link-type-source-label"), { target: { value: "Gleich" } });
    fireEvent.click(screen.getByTestId("link-type-offer_ticket_ci"));
    request.mockImplementation((async (method: string, path: string, opts?: { body?: unknown }) => {
      if (method === "PATCH") {
        expect(path).toBe("/api/v1/admin/itsm/link-types/1");
        expect(opts?.body).toEqual({ source_label: "Gleich", offer_ticket_ci: true });
        return ROWS[0];
      }
      return reads(method, path);
    }) as never);
    fireEvent.click(screen.getByTestId("link-type-save"));
    await waitFor(() => expect(screen.queryByTestId("link-type-dialog")).toBeNull());
    expect(request.mock.calls.some((c: unknown[]) => c[0] === "PATCH")).toBe(true);
  });

  it("offers propagation for a Tiqora type in Mode B", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("link-type-edit-Affects"));
    fireEvent.click(screen.getByTestId("link-type-propagation"));
    fireEvent.click(screen.getByTestId("link-type-propagation-menu-option-both"));
    request.mockImplementation((async (method: string, _path: string, opts?: { body?: unknown }) => {
      if (method === "PATCH") {
        expect(opts?.body).toEqual({ propagation: "both" });
        return ROWS[1];
      }
      return reads(method, _path);
    }) as never);
    fireEvent.click(screen.getByTestId("link-type-save"));
    await waitFor(() => expect(request.mock.calls.some((c: unknown[]) => c[0] === "PATCH")).toBe(true));
  });

  it("hides propagation entirely outside Mode B", async () => {
    mode = "znuny_readthrough";
    renderPage();
    fireEvent.click(await screen.findByTestId("link-type-edit-Affects"));
    expect(screen.getByTestId("link-type-dialog")).toBeInTheDocument();
    expect(screen.queryByTestId("link-type-propagation")).toBeNull();
  });

  it("shows the refusal of a save", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("link-type-edit-Normal"));
    request.mockImplementation((async (method: string, path: string) => {
      if (method === "PATCH") {
        throw new ApiError(422, { detail: { code: "znuny_link_type_read_only" } }, path);
      }
      return reads(method, path);
    }) as never);
    fireEvent.click(screen.getByTestId("link-type-valid"));
    fireEvent.click(screen.getByTestId("link-type-save"));
    expect(await screen.findByTestId("link-type-error")).toHaveTextContent(
      i18n.t("admin.itsm.linkTypes.errors.znuny_link_type_read_only"),
    );
  });

  it("calls the sync route", async () => {
    renderPage();
    await screen.findByTestId("link-type-row-Normal");
    request.mockImplementation((async (method: string, path: string) =>
      method === "POST" ? (expect(path).toBe("/api/v1/admin/itsm/link-types/sync"), { changed: 2 }) : reads(method, path)) as never);
    fireEvent.click(screen.getByTestId("link-types-sync"));
    expect(await screen.findByTestId("link-types-sync-done")).toHaveTextContent("2");
  });

  it("shows a failed sync", async () => {
    renderPage();
    await screen.findByTestId("link-type-row-Normal");
    request.mockImplementation((async (method: string, path: string) => {
      if (method === "POST") throw new ApiError(500, { detail: "boom" }, path);
      return reads(method, path);
    }) as never);
    fireEvent.click(screen.getByTestId("link-types-sync"));
    expect(await screen.findByTestId("link-types-sync-error")).toHaveTextContent(i18n.t("itsm.errors.generic"));
    expect(screen.queryByTestId("link-types-sync-done")).toBeNull();
  });
});
