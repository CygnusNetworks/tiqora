import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  createMemoryHistory,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import type { QueueNode } from "@/lib/api";
import { QueueShortcutCard } from "./QueueShortcutCard";

const queueWithNew: QueueNode = {
  id: 7,
  name: "Support::Level 1",
  group_id: 1,
  valid: true,
  counts: { open: 12, new: 3, locked: 0, unlocked: 12, total: 12 },
  children: [],
};

const queueNoNew: QueueNode = {
  id: 8,
  name: "Billing",
  group_id: 1,
  valid: true,
  counts: { open: 5, new: 0, locked: 0, unlocked: 5, total: 5 },
  children: [],
};

/**
 * Render a component that uses TanStack `Link` inside a minimal in-memory
 * router so the anchors resolve real hrefs. The component under test is the
 * root route's element.
 */
async function renderInRouter(ui: React.ReactElement) {
  const rootRoute = createRootRoute({ component: () => ui });
  const queuesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent/queues",
    validateSearch: (s: Record<string, unknown>) => s,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([queuesRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  return render(
    <I18nextProvider i18n={i18n}>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <RouterProvider router={router as any} />
    </I18nextProvider>,
  );
}

describe("QueueShortcutCard", () => {
  it("renders the open count linking to the open view", async () => {
    await renderInRouter(<QueueShortcutCard queue={queueWithNew} />);
    const openLink = await screen.findByTestId("queue-shortcut-7-open");
    expect(openLink).toHaveTextContent("12");
    const href = openLink.getAttribute("href") ?? "";
    expect(href).toContain("queue_id=7");
    expect(href).toContain("state_type=open");
  });

  it("renders an accent new badge linking to the new view when new > 0", async () => {
    await renderInRouter(<QueueShortcutCard queue={queueWithNew} />);
    const newLink = await screen.findByTestId("queue-shortcut-7-new");
    expect(newLink).toHaveTextContent("3");
    const href = newLink.getAttribute("href") ?? "";
    expect(href).toContain("queue_id=7");
    expect(href).toContain("state_type=new");
    // Accent-tinted pill (sidebar badge language).
    expect(newLink.className).toContain("bg-accent-dim");
    expect(newLink.className).toContain("text-accent");
  });

  it("omits the new badge when new is 0", async () => {
    await renderInRouter(<QueueShortcutCard queue={queueNoNew} />);
    expect(await screen.findByTestId("queue-shortcut-8-open")).toBeInTheDocument();
    expect(screen.queryByTestId("queue-shortcut-8-new")).not.toBeInTheDocument();
  });
});
