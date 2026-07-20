import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  addNotification,
  clearNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  useNotifications,
} from "./notificationStore";

describe("notificationStore", () => {
  beforeEach(() => {
    clearNotifications();
  });

  it("adds a notification and increments the unread count", () => {
    const { result } = renderHook(() => useNotifications());
    act(() => {
      addNotification({ ticketId: 1, tn: "T1", title: "Hi", queueName: "Raw" });
    });
    expect(result.current.items).toHaveLength(1);
    expect(result.current.unreadCount).toBe(1);
    expect(result.current.items[0]).toMatchObject({ ticketId: 1, tn: "T1", read: false });
  });

  it("keeps newest first and counts all unread", () => {
    const { result } = renderHook(() => useNotifications());
    act(() => {
      addNotification({ ticketId: 1, tn: "T1", title: "a", queueName: "Q" });
      addNotification({ ticketId: 2, tn: "T2", title: "b", queueName: "Q" });
    });
    expect(result.current.items.map((i) => i.ticketId)).toEqual([2, 1]);
    expect(result.current.unreadCount).toBe(2);
  });

  it("markAllNotificationsRead zeroes the unread count", () => {
    const { result } = renderHook(() => useNotifications());
    act(() => {
      addNotification({ ticketId: 1, tn: "T1", title: "a", queueName: "Q" });
    });
    act(() => {
      markAllNotificationsRead();
    });
    expect(result.current.unreadCount).toBe(0);
    expect(result.current.items[0].read).toBe(true);
  });

  it("markNotificationRead marks a single item read", () => {
    const { result } = renderHook(() => useNotifications());
    let secondId = "";
    act(() => {
      addNotification({ ticketId: 1, tn: "T1", title: "a", queueName: "Q" });
      secondId = addNotification({ ticketId: 2, tn: "T2", title: "b", queueName: "Q" }).id;
    });
    act(() => {
      markNotificationRead(secondId);
    });
    expect(result.current.unreadCount).toBe(1);
    expect(result.current.items.find((i) => i.id === secondId)?.read).toBe(true);
  });
});
