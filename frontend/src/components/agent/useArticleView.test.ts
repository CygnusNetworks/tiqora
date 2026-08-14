import { describe, it, expect, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import type { ArticleListItem } from "@/lib/api";
import { useArticleView } from "./useArticleView";

function article(overrides: Partial<ArticleListItem>): ArticleListItem {
  return {
    id: 1,
    communication_channel_id: 1,
    communication_channel_name: null,
    sender_type: "customer",
    is_visible_for_customer: true,
    ...overrides,
  } as ArticleListItem;
}

describe("useArticleView auto-detect", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("auto-switches to conversation view for a Telegram-dominant ticket", () => {
    const articles = [
      article({ id: 1, sender_type: "customer", communication_channel_name: "Telegram" }),
      article({ id: 2, sender_type: "agent", communication_channel_name: "Telegram" }),
    ];
    const { result } = renderHook(() => useArticleView(1, articles));
    expect(result.current.view).toBe("conversation");
    expect(result.current.isAuto).toBe(true);
  });

  it("keeps the split view for an Email-dominant ticket", () => {
    const articles = [
      article({ id: 1, sender_type: "customer", communication_channel_name: "Email" }),
    ];
    const { result } = renderHook(() => useArticleView(2, articles));
    expect(result.current.view).toBe("split");
  });
});
