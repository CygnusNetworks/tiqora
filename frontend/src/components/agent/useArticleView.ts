import { useState } from "react";
import type { ArticleListItem } from "@/lib/api";
import { dominantChannel, isConversationalChannel } from "@/lib/articleChannel";

export type ArticleViewMode = "split" | "conversation";

const STORAGE_KEY = "tiqora-article-view";

type Overrides = Record<string, ArticleViewMode>;

function readOverrides(): Overrides {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Overrides) : {};
  } catch {
    return {};
  }
}

function writeOverride(ticketId: number, view: ArticleViewMode) {
  if (typeof window === "undefined") return;
  try {
    const overrides = readOverrides();
    overrides[String(ticketId)] = view;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides));
  } catch {
    // best-effort persistence only
  }
}

function autoViewFor(articles: ArticleListItem[]): ArticleViewMode {
  const channel = dominantChannel(articles);
  return channel != null && isConversationalChannel(channel) ? "conversation" : "split";
}

/**
 * Picks the article view (split vs. conversation) per ticket: auto-detected
 * from the dominant customer-article channel, unless the agent has manually
 * switched for this ticket before (remembered in localStorage — that
 * override then wins and drops the "Auto" badge).
 */
export function useArticleView(ticketId: number, articles: ArticleListItem[]) {
  // Re-read on ticket change via key-less state + explicit reset below would
  // need an effect; simplest correct approach is deriving fresh each render
  // from a small bit of local state that only stores the *manual* choice.
  const [overrides, setOverridesState] = useState<Overrides>(readOverrides);

  const stored = overrides[String(ticketId)];
  const auto = autoViewFor(articles);
  const view = stored ?? auto;
  const isAuto = stored === undefined;

  const setView = (next: ArticleViewMode) => {
    writeOverride(ticketId, next);
    setOverridesState((prev) => ({ ...prev, [String(ticketId)]: next }));
  };

  return { view, isAuto, setView };
}
