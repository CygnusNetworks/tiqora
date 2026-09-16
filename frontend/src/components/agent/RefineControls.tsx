import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { Menu, MenuItem, MenuLabel } from "@/components/ui/Menu";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { applyRefined, ownSections, segmentBody } from "@/lib/replyQuote";
import {
  REFINE_TONES,
  refineApi,
  type RefineTarget,
  type RefineTone,
} from "@/lib/refineApi";
import { loadRefineTone, saveRefineTone } from "@/lib/refineTone";

/**
 * "Text verfeinern" for a composer body: one split button — press the left
 * half to run, the caret to pick the tone — plus a chip naming the tone that
 * a press would use, and an undo that appears after every run.
 *
 * It is a split button rather than a select plus a button because the action
 * is one line in a dialog that is already tall, and because a `SelectField`
 * here fought its own width: `cn()` is plain `clsx`, so a width passed in
 * lands beside the component's own `w-full` and loses.
 *
 * Only the agent's own text is rewritten. The body is segmented by
 * `@/lib/replyQuote`; quote segments are sent along as context so an inline
 * answer ("Ja, das passt.") can be rewritten against the line it answers, but
 * the new body is re-assembled from the ORIGINAL quote bytes — quoted text
 * cannot change, whatever the model returns.
 *
 * Renders nothing unless the queue has `enabled_refine` and the agent's ACL
 * allows the feature, so a composer in a non-AI queue looks exactly as before.
 */
export function RefineControls({
  target,
  body,
  onChange,
  disabled,
  testIdPrefix = "refine",
}: {
  /** `{ticket_id}` when replying inside a ticket (the server reads its queue),
   * `{queue_id}` for the New-ticket form, `null` while no queue is picked
   * there — the controls stay hidden until one is. */
  target: RefineTarget | null;
  body: string;
  onChange: (body: string) => void;
  disabled?: boolean;
  testIdPrefix?: string;
}) {
  const { t } = useTranslation();
  const [tone, setTone] = useState<RefineTone>(loadRefineTone);
  /** The body as the agent last typed it, kept so one refine can be undone. */
  const [beforeRefine, setBeforeRefine] = useState<string | null>(null);

  const availabilityQ = useQuery({
    queryKey: ["ai-refine-availability", target],
    queryFn: () => refineApi.refineAvailability(target as RefineTarget),
    enabled: target != null,
    staleTime: 5 * 60 * 1000,
  });

  const segments = segmentBody(body);
  const sections = ownSections(segments);

  const refineMutation = useMutation({
    mutationFn: () =>
      refineApi.refine({ ...(target as RefineTarget), tone, segments }),
    onSuccess: (response) => {
      const refined = new Map(response.sections.map((s) => [s.id, s.text]));
      setBeforeRefine(body);
      onChange(applyRefined(segments, refined));
    },
  });

  if (target == null || !availabilityQ.data?.available) return null;

  const busy = refineMutation.isPending;
  const nothingToRefine = sections.length === 0;
  const canRefine = !disabled && !busy && !nothingToRefine;

  const pickTone = (next: RefineTone) => {
    setTone(next);
    saveRefineTone(next);
  };

  const undo = () => {
    if (beforeRefine === null) return;
    onChange(beforeRefine);
    setBeforeRefine(null);
    refineMutation.reset();
  };

  const half =
    "px-2 py-1 text-xs text-ink transition-colors duration-100 disabled:cursor-not-allowed disabled:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="inline-flex overflow-hidden rounded-md border border-hairline bg-surface">
        <button
          type="button"
          data-testid={`${testIdPrefix}-button`}
          disabled={!canRefine}
          title={t("ticket.refine.hint")}
          onClick={() => refineMutation.mutate()}
          className={cn(half, "enabled:hover:bg-surface-subtle")}
        >
          {busy ? (
            <span className="flex items-center gap-1.5">
              <Spinner className="h-3 w-3" />
              {t("ticket.refine.running")}
            </span>
          ) : (
            `✨ ${t("ticket.refine.button")}`
          )}
        </button>
        <Menu
          align="left"
          panelTestId={`${testIdPrefix}-tone-menu`}
          trigger={({ ref, toggleProps }) => (
            <button
              ref={ref}
              type="button"
              data-testid={`${testIdPrefix}-tone-trigger`}
              aria-label={t("ticket.refine.toneMenuLabel")}
              disabled={busy}
              {...toggleProps}
              className={cn(
                half,
                "border-l border-hairline text-muted hover:text-ink",
              )}
            >
              ▾
            </button>
          )}
        >
          <MenuLabel>{t("ticket.refine.toneLabel")}</MenuLabel>
          {REFINE_TONES.map((value) => (
            <MenuItem
              key={value}
              testId={`${testIdPrefix}-tone-${value}`}
              selected={value === tone}
              onSelect={() => pickTone(value)}
            >
              {t(toneLabelKey(value))}
            </MenuItem>
          ))}
        </Menu>
      </span>

      {/* Names what a press would do, and doubles as the reason it cannot. */}
      <span
        className={cn("text-muted", nothingToRefine && "italic")}
        data-testid={`${testIdPrefix}-tone-chip`}
      >
        {nothingToRefine
          ? t("ticket.refine.nothingToRefineShort")
          : t(toneLabelKey(tone))}
      </span>

      {beforeRefine !== null && (
        <button
          type="button"
          data-testid={`${testIdPrefix}-undo`}
          disabled={busy}
          onClick={undo}
          className="rounded px-1.5 py-1 text-muted transition-colors duration-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          ↩ {t("ticket.refine.undo")}
        </button>
      )}

      {refineMutation.isError && (
        <span className="text-danger" data-testid={`${testIdPrefix}-error`}>
          {refineErrorMessage(refineMutation.error, t)}
        </span>
      )}
    </div>
  );
}

function toneLabelKey(tone: RefineTone): string {
  return `ticket.refine.tone${tone[0].toUpperCase()}${tone.slice(1)}`;
}

/** Maps the structured `"<code>: <message>"` detail the API returns onto a
 * specific hint; anything unrecognised falls back to the generic failure.
 *
 * Reads `message`, not `detail`: `detail` is the parsed response body, so for
 * a FastAPI error it is the OBJECT `{detail: "..."}` and every prefix test
 * below would silently miss. `ApiError` unwraps that into `message` — the same
 * property `ReplyDialog` matches on. */
function refineErrorMessage(
  error: unknown,
  t: (key: string) => string,
): string {
  if (!(error instanceof ApiError)) return t("ticket.refine.error");
  const detail = error.message;
  if (error.status === 429) return t("ticket.refine.errorLimit");
  if (error.status === 403) return t("ticket.refine.errorDenied");
  if (detail.startsWith("refine_disabled"))
    return t("ticket.refine.errorDisabled");
  if (detail.startsWith("refine_empty_output"))
    return t("ticket.refine.errorEmpty");
  return t("ticket.refine.error");
}
