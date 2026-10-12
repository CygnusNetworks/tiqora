import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import i18n from "@/i18n";
import { ProcessDesignerPage } from "./ProcessDesignerPage";

const getProcess = vi.fn();
const getCatalog = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ processEntityId: "Process-1" }),
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode; [k: string]: unknown }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/processDesignerApi", () => ({
  processDesignerApi: {
    getProcess: (...args: unknown[]) => getProcess(...args),
    catalog: (...args: unknown[]) => getCatalog(...args),
  },
}));

// The canvas needs layout APIs jsdom lacks; its own rendering is covered by the e2e/demo check.
vi.mock("@/components/process-designer/DesignerCanvas", () => ({
  DesignerCanvas: () => <div data-testid="process-designer-canvas" />,
}));

const entity = (entity_id: string, name: string, config: Record<string, unknown> = {}) => ({
  entity_type: "Activity" as const,
  entity_id,
  id: 1,
  name,
  config,
  version: "v1",
  sync_state: "synced" as const,
  changed_at: "2026-10-01T10:00:00Z",
  changed_by: 1,
});

const draft = {
  process: { ...entity("Process-1", "Bestellung"), entity_type: "Process" as const },
  state_entity_id: "S1",
  state_name: "Active",
  layout: {},
  activities: [entity("Activity-a", "Antrag erfassen"), entity("Activity-b", "Genehmigung")],
  activity_dialogs: [],
  transitions: [],
  transition_actions: [],
  missing_references: [],
  pending_changes: 0,
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ProcessDesignerPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

function setViewport(phone: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: phone,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
}

describe("ProcessDesignerPage", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    getProcess.mockResolvedValue(draft);
    getCatalog.mockResolvedValue({ actions: [], condition_types: [], condition_modules: [], fields: [], interfaces: [], permissions: [], account_time: false });
  });
  afterEach(() => {
    // @ts-expect-error restore the jsdom default (no matchMedia)
    delete window.matchMedia;
    vi.clearAllMocks();
  });

  it("renders the layout grid with palette, canvas and panel on a wide screen", async () => {
    setViewport(false);
    renderPage();
    expect(await screen.findByTestId("process-designer-layout")).toBeInTheDocument();
    expect(screen.getByTestId("process-designer-palette")).toBeInTheDocument();
    expect(screen.getByTestId("process-designer-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("process-designer-panel")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Bestellung" })).toBeInTheDocument();
    expect(getProcess.mock.calls[0][0]).toBe("Process-1");
  });

  it("phone width renders the read-only list", async () => {
    setViewport(true);
    renderPage();
    expect(await screen.findByTestId("process-designer-phone-list")).toBeInTheDocument();
    expect(screen.queryByTestId("process-designer-layout")).not.toBeInTheDocument();
    expect(screen.queryByTestId("process-designer-canvas")).not.toBeInTheDocument();
    expect(screen.getByTestId("process-designer-activity-Activity-a")).toHaveTextContent("Antrag erfassen");
    expect(screen.getByText(i18n.t("admin.processDesigner.phoneReadOnly"))).toBeInTheDocument();
  });

  it("shows an error when the draft cannot be loaded", async () => {
    setViewport(false);
    getProcess.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText(i18n.t("admin.processDesigner.loadError"))).toBeInTheDocument();
  });

  it("designer route is lazy: the page module is not imported by router.tsx", () => {
    const src = readFileSync(resolve(__dirname, "../../../router.tsx"), "utf8");
    expect(src).not.toMatch(/^import[^;]*ProcessDesignerPage[^;]*from/m);
    expect(src).toMatch(/lazyRouteComponent\(\s*\(\) => import\("@\/routes\/admin\/process-designer\/ProcessDesignerPage"\)/);
  });
});
