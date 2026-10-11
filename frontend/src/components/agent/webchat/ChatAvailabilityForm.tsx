import { useEffect, useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { MultiSelectInput } from "@/components/fields/MultiSelectInput";
import { Switch } from "@/components/ui/Switch";
import {
  chatAgentStateKey,
  chatTopicsKey,
  webchatApi,
  type ChatAgentState,
} from "@/lib/webchatApi";

const MAX_CONCURRENT_MIN = 1;
const MAX_CONCURRENT_MAX = 20;

/** Asks for desktop-notification permission once; never throws, never on page load. */
async function askNotificationPermissionOnce(): Promise<void> {
  if (typeof Notification === "undefined" || Notification.permission !== "default") return;
  try {
    await Promise.resolve(Notification.requestPermission());
  } catch {
    // Some browsers only allow this from a user gesture or not at all.
  }
}

type Desired = { available: boolean; topics: string[]; max_concurrent: number };

function toDesired(s: ChatAgentState): Desired {
  return { available: s.available, topics: s.topics, max_concurrent: s.max_concurrent };
}

/** The draft as an integer within 1-20, or null when empty / out of range. */
function parseMax(draft: string): number | null {
  if (!/^\s*\d+\s*$/.test(draft)) return null;
  const n = Number(draft);
  return n >= MAX_CONCURRENT_MIN && n <= MAX_CONCURRENT_MAX ? n : null;
}

/** Availability switch, topic filter and concurrency limit of the agent's chats. */
export function ChatAvailabilityForm() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const ids = useId();
  const stateQ = useQuery({
    queryKey: chatAgentStateKey,
    queryFn: ({ signal }) => webchatApi.getAgentState(signal),
  });
  const topicsQ = useQuery({
    queryKey: chatTopicsKey,
    queryFn: ({ signal }) => webchatApi.topics(signal),
  });
  const state = stateQ.data;
  const [maxDraft, setMaxDraft] = useState("3");
  const [maxInvalid, setMaxInvalid] = useState(false);
  const [error, setError] = useState(false);
  // Saves are queued in order so editing the limit and then clicking the
  // switch loses neither; `desired` is what the next save sends, `good` the
  // last state the server confirmed (the target of a rollback).
  const desired = useRef<Desired | null>(null);
  const good = useRef<Desired | null>(null);
  const lastSentMax = useRef<number | null>(null);
  const inflight = useRef(0);
  const chain = useRef<Promise<unknown>>(Promise.resolve());

  useEffect(() => {
    if (!state || inflight.current > 0) return;
    good.current = toDesired(state);
    desired.current = toDesired(state);
    lastSentMax.current = state.max_concurrent;
    setMaxDraft(String(state.max_concurrent));
    setMaxInvalid(false);
  }, [state]);

  if (!state) {
    return (
      <p className="px-3 py-3 text-[12.5px] text-muted" data-testid="chat-availability-loading">
        {stateQ.isError ? t("webchat.agent.actionFailed") : t("common.loading")}
      </p>
    );
  }

  const push = (patch: Partial<Desired>) => {
    const body = { ...(desired.current ?? toDesired(state)), ...patch };
    desired.current = body;
    if (patch.max_concurrent !== undefined) lastSentMax.current = patch.max_concurrent;
    inflight.current += 1;
    chain.current = chain.current
      .then(() => webchatApi.putAgentState(body))
      .then(
        (data) => {
          good.current = toDesired(data);
          setError(false);
          qc.setQueryData(chatAgentStateKey, data);
        },
        () => {
          // Roll the field back to what the server has and say so.
          const back = good.current ?? toDesired(state);
          desired.current = back;
          lastSentMax.current = back.max_concurrent;
          setMaxDraft(String(back.max_concurrent));
          setMaxInvalid(false);
          setError(true);
          void qc.invalidateQueries({ queryKey: chatAgentStateKey });
        },
      )
      .finally(() => {
        inflight.current -= 1;
      });
  };

  const options = (topicsQ.data ?? []).map((tp) => ({ value: tp.ref, label: tp.label }));
  const commitMax = () => {
    const next = parseMax(maxDraft);
    if (next === null) {
      setMaxInvalid(true);
      return;
    }
    setMaxInvalid(false);
    // Enter followed by blur must not send the same value twice.
    if (next !== lastSentMax.current) push({ max_concurrent: next });
  };

  return (
    <div className="space-y-3 px-3 py-3" data-testid="chat-availability-form">
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={`${ids}-available`} className="text-[13px] font-medium text-ink">
          {t("webchat.agent.inbox.accepting")}
        </label>
        <Switch
          id={`${ids}-available`}
          testId="chat-available-switch"
          checked={state.available}
          onChange={(on) => {
            if (on) void askNotificationPermissionOnce();
            push({ available: on });
          }}
        />
      </div>
      <div>
        <div id={`${ids}-topics`} className="mb-1 text-[12px] font-medium text-muted">
          {t("webchat.agent.inbox.topics")}
        </div>
        <MultiSelectInput
          testId="chat-topics-select"
          labelId={`${ids}-topics`}
          value={state.topics}
          options={options}
          onChange={(next) => push({ topics: next })}
        />
        <p className="mt-1 text-[11.5px] text-muted">{t("webchat.agent.inbox.topicsHint")}</p>
      </div>
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={`${ids}-max`} className="text-[12.5px] text-ink">
          {t("webchat.agent.inbox.maxConcurrent")}
        </label>
        <input
          id={`${ids}-max`}
          data-testid="chat-max-concurrent"
          type="number"
          inputMode="numeric"
          min={MAX_CONCURRENT_MIN}
          max={MAX_CONCURRENT_MAX}
          value={maxDraft}
          aria-invalid={maxInvalid || undefined}
          aria-describedby={maxInvalid ? `${ids}-max-err` : undefined}
          onChange={(e) => {
            setMaxDraft(e.target.value);
            setMaxInvalid(false);
          }}
          onBlur={commitMax}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitMax();
          }}
          className="h-8 w-16 rounded border border-hairline bg-surface px-2 text-right text-[13px] tabular-nums text-ink"
        />
      </div>
      {maxInvalid && (
        <p id={`${ids}-max-err`} role="alert" className="text-[12px] text-danger" data-testid="chat-max-invalid">
          {t("webchat.agent.inbox.maxInvalid", { min: MAX_CONCURRENT_MIN, max: MAX_CONCURRENT_MAX })}
        </p>
      )}
      {error && (
        <p role="alert" className="text-[12px] text-danger" data-testid="chat-availability-error">
          {t("webchat.agent.actionFailed")}
        </p>
      )}
    </div>
  );
}
