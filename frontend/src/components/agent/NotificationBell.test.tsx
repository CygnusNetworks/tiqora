import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { NotificationBell, NotificationToaster } from "./NotificationBell";
import { addNotification, clearNotifications } from "@/lib/notificationStore";

const { navigate } = vi.hoisted(() => ({
  navigate: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

function renderBell() {
  return render(
    <I18nextProvider i18n={i18n}>
      <NotificationBell />
    </I18nextProvider>,
  );
}

function renderToaster() {
  return render(
    <I18nextProvider i18n={i18n}>
      <NotificationToaster />
    </I18nextProvider>,
  );
}

describe("NotificationBell", () => {
  beforeEach(() => {
    navigate.mockClear();
    clearNotifications();
    void i18n.changeLanguage("en");
  });

  it("shows no unread badge when there are no notifications", () => {
    renderBell();
    expect(screen.queryByTestId("notification-unread-count")).not.toBeInTheDocument();
  });

  it("shows the unread count on the badge", () => {
    addNotification({ ticketId: 1, tn: "T1", title: "First", queueName: "Q1" });
    addNotification({ ticketId: 2, tn: "T2", title: "Second", queueName: "Q2" });
    renderBell();
    expect(screen.getByTestId("notification-unread-count")).toHaveTextContent("2");
  });

  it("caps the badge display at 9+", () => {
    for (let i = 0; i < 12; i += 1) {
      addNotification({ ticketId: i, tn: `T${i}`, title: `Item ${i}`, queueName: "Q" });
    }
    renderBell();
    expect(screen.getByTestId("notification-unread-count")).toHaveTextContent("9+");
  });

  it("opens the dropdown, marks all as read, and closes on backdrop click", () => {
    addNotification({ ticketId: 1, tn: "T1", title: "First", queueName: "Q1" });
    renderBell();

    expect(screen.queryByTestId("notification-panel")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("notification-bell"));
    expect(screen.getByTestId("notification-panel")).toBeInTheDocument();
    // Opening marks everything read, so the unread badge disappears.
    expect(screen.queryByTestId("notification-unread-count")).not.toBeInTheDocument();

    // Close via the backdrop button (aria-hidden, fixed inset-0).
    fireEvent.click(screen.getByRole("button", { name: "", hidden: true }));
    expect(screen.queryByTestId("notification-panel")).not.toBeInTheDocument();
  });

  it("shows an empty state message when there are no items", () => {
    renderBell();
    fireEvent.click(screen.getByTestId("notification-bell"));
    expect(screen.getByText("No new notifications.")).toBeInTheDocument();
  });

  it("lists items and shows the fallback subject when title is empty", () => {
    addNotification({ ticketId: 5, tn: "T5", title: "", queueName: "Support" });
    renderBell();
    fireEvent.click(screen.getByTestId("notification-bell"));
    const item = screen.getByTestId("notification-item-5");
    expect(item).toHaveTextContent("T5");
    expect(item).toHaveTextContent("Support");
    expect(item).toHaveTextContent("(no subject)");
  });

  it("navigates to the ticket, marks it read, and closes the panel on item click", () => {
    addNotification({ ticketId: 9, tn: "T9", title: "Item 9", queueName: "Q" });
    renderBell();
    fireEvent.click(screen.getByTestId("notification-bell"));
    fireEvent.click(screen.getByTestId("notification-item-9"));

    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/$ticketId",
      params: { ticketId: "9" },
    });
    expect(screen.queryByTestId("notification-panel")).not.toBeInTheDocument();
  });
});

describe("NotificationToaster", () => {
  beforeEach(() => {
    navigate.mockClear();
    clearNotifications();
    void i18n.changeLanguage("en");
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing when there are no notifications", () => {
    renderToaster();
    expect(screen.queryByTestId("notification-toaster")).not.toBeInTheDocument();
  });

  it("does not toast items that existed before the toaster mounted", () => {
    addNotification({ ticketId: 1, tn: "T1", title: "Old", queueName: "Q" });
    renderToaster();
    expect(screen.queryByTestId("notification-toaster")).not.toBeInTheDocument();
  });

  it("toasts the very first item to arrive after an empty-store mount", () => {
    // Fixed: the initial-snapshot skip is now recorded on mount even when
    // the store starts empty, so a genuinely new arrival right after mount
    // is toasted instead of being silently adopted as the new baseline.
    renderToaster();
    act(() => {
      addNotification({ ticketId: 1, tn: "T1", title: "First ever", queueName: "Q" });
    });
    expect(screen.getByTestId("notification-toaster")).toBeInTheDocument();
    expect(screen.getByTestId("notification-toast-1")).toHaveTextContent("First ever");
  });

  it("toasts a newly-arrived notification and auto-dismisses it after 5s", () => {
    addNotification({ ticketId: 1, tn: "T1", title: "Old", queueName: "Q" });
    renderToaster();

    act(() => {
      addNotification({ ticketId: 2, tn: "T2", title: "Fresh", queueName: "Q" });
    });

    expect(screen.getByTestId("notification-toaster")).toBeInTheDocument();
    expect(screen.getByTestId("notification-toast-2")).toHaveTextContent("Fresh");

    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(screen.queryByTestId("notification-toaster")).not.toBeInTheDocument();
  });

  it("auto-dismisses each staggered toast on its own timer, not just the latest", () => {
    addNotification({ ticketId: 1, tn: "T1", title: "Old", queueName: "Q" });
    renderToaster();

    act(() => {
      addNotification({ ticketId: 2, tn: "T2", title: "First", queueName: "Q" });
    });
    // 2s later a second notification arrives; it must not cancel the first
    // toast's pending auto-dismiss timer (the bug this guards against).
    act(() => {
      vi.advanceTimersByTime(2000);
      addNotification({ ticketId: 3, tn: "T3", title: "Second", queueName: "Q" });
    });

    expect(screen.getByTestId("notification-toast-2")).toBeInTheDocument();
    expect(screen.getByTestId("notification-toast-3")).toBeInTheDocument();

    // At 5s from the first arrival, only the first toast auto-dismisses.
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.queryByTestId("notification-toast-2")).not.toBeInTheDocument();
    expect(screen.getByTestId("notification-toast-3")).toBeInTheDocument();

    // The second toast dismisses 5s after its own arrival.
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.queryByTestId("notification-toaster")).not.toBeInTheDocument();
  });

  it("dismisses and navigates when a toast is clicked", () => {
    addNotification({ ticketId: 1, tn: "T1", title: "Old", queueName: "Q" });
    renderToaster();

    act(() => {
      addNotification({ ticketId: 3, tn: "T3", title: "Click me", queueName: "Q" });
    });

    fireEvent.click(screen.getByTestId("notification-toast-3"));

    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/tickets/$ticketId",
      params: { ticketId: "3" },
    });
    expect(screen.queryByTestId("notification-toaster")).not.toBeInTheDocument();
  });

  it("keeps at most 3 toasts at a time", () => {
    renderToaster();

    act(() => {
      addNotification({ ticketId: 1, tn: "T1", title: "One", queueName: "Q" });
    });
    act(() => {
      addNotification({ ticketId: 2, tn: "T2", title: "Two", queueName: "Q" });
    });
    act(() => {
      addNotification({ ticketId: 3, tn: "T3", title: "Three", queueName: "Q" });
    });
    act(() => {
      addNotification({ ticketId: 4, tn: "T4", title: "Four", queueName: "Q" });
    });

    expect(screen.queryByTestId("notification-toast-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("notification-toast-2")).toBeInTheDocument();
    expect(screen.getByTestId("notification-toast-3")).toBeInTheDocument();
    expect(screen.getByTestId("notification-toast-4")).toBeInTheDocument();
  });
});
