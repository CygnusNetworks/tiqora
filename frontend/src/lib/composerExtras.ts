import type { QueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { survivingMentions, type PickedMention } from "@/lib/mentions";

/** Which side writes failed, so the composer can name them and offer a retry. */
export type ComposerExtrasResult = { failed: Array<"mentions" | "time"> };

/**
 * Records what the composer collected alongside the article: the colleagues
 * still `@`-named in the body, and the minutes typed into the footer chip.
 *
 * Runs *after* the article is created — the reply is the thing that must not
 * be lost. Failures are returned rather than thrown so the caller can keep the
 * composer open with a retry instead of silently dropping a booking; retrying
 * only re-runs this, never the send.
 */
export async function postComposerExtras(
  ticketId: number,
  {
    body,
    mentions,
    timeUnits,
    queryClient,
  }: {
    body: string;
    mentions: PickedMention[];
    /** Raw field value; blank or non-positive books nothing. */
    timeUnits: string;
    queryClient: QueryClient;
  },
): Promise<ComposerExtrasResult> {
  const failed: Array<"mentions" | "time"> = [];
  const toMention = survivingMentions(body, mentions);
  const units = Number(timeUnits);
  const booking = timeUnits.trim() !== "" && Number.isFinite(units) && units > 0;

  if (toMention.length > 0) {
    try {
      await Promise.all(
        toMention.map((m) => api.createTicketMention(ticketId, { user_id: m.id })),
      );
      void queryClient.invalidateQueries({ queryKey: ["tickets", ticketId, "mentions"] });
    } catch {
      failed.push("mentions");
    }
  }

  if (booking) {
    try {
      await api.createTicketTimeAccounting(ticketId, { time_unit: units });
      void queryClient.invalidateQueries({
        queryKey: ["tickets", ticketId, "time-accounting"],
      });
    } catch {
      failed.push("time");
    }
  }

  return { failed };
}
