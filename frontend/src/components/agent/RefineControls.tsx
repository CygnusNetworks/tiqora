import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Spinner } from "@/components/ui/Spinner";
import { applyRefined, ownSections, segmentBody } from "@/lib/replyQuote";
import {
  REFINE_TONES,
  refineApi,
  type RefineTarget,
  type RefineTone,
} from "@/lib/refineApi";

/**
 * "Text verfeinern" toolbar for a composer body: a tone picker, the trigger,
 * and an undo that reappears after every run.
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
  const [tone, setTone] = useState<RefineTone>("standard");
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
  const canRefine = !disabled && !busy && sections.length > 0;

  const undo = () => {
    if (beforeRefine === null) return;
    onChange(beforeRefine);
    setBeforeRefine(null);
    refineMutation.reset();
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <SelectField
        items={REFINE_TONES.map((value) => ({
          value,
          label: t(
            `ticket.refine.tone${value[0].toUpperCase()}${value.slice(1)}`,
          ),
        }))}
        value={tone}
        onChange={(next) => setTone(next)}
        disabled={busy}
        testId={`${testIdPrefix}-tone-select`}
        aria-label={t("ticket.refine.toneLabel")}
        className="w-36"
      />
      <Button
        variant="ghost"
        size="sm"
        data-testid={`${testIdPrefix}-button`}
        disabled={!canRefine}
        title={
          sections.length === 0
            ? t("ticket.refine.nothingToRefine")
            : t("ticket.refine.hint")
        }
        onClick={() => refineMutation.mutate()}
      >
        {busy ? (
          <span className="flex items-center gap-1.5">
            <Spinner className="h-3 w-3" />
            {t("ticket.refine.running")}
          </span>
        ) : (
          `✨ ${t("ticket.refine.button")}`
        )}
      </Button>
      {beforeRefine !== null && (
        <Button
          variant="ghost"
          size="sm"
          data-testid={`${testIdPrefix}-undo`}
          disabled={busy}
          onClick={undo}
        >
          ↩ {t("ticket.refine.undo")}
        </Button>
      )}
      {refineMutation.isError && (
        <span className="text-danger" data-testid={`${testIdPrefix}-error`}>
          {refineErrorMessage(refineMutation.error, t)}
        </span>
      )}
    </div>
  );
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
