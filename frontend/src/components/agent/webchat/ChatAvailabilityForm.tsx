import { useEffect, useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

function clampMax(n: number): number {
  if (!Number.isFinite(n)) return MAX_CONCURRENT_MIN;
  return Math.min(MAX_CONCURRENT_MAX, Math.max(MAX_CONCURRENT_MIN, Math.round(n)));
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
  const [error, setError] = useState(false);

  useEffect(() => {
    if (state) setMaxDraft(String(state.max_concurrent));
  }, [state]);

  const save = useMutation({
    mutationFn: (next: { available: boolean; topics: string[]; max_concurrent: number }) =>
      webchatApi.putAgentState(next),
    onSuccess: (data: ChatAgentState) => {
      setError(false);
      qc.setQueryData(chatAgentStateKey, data);
    },
    onError: () => {
      setError(true);
      void qc.invalidateQueries({ queryKey: chatAgentStateKey });
    },
  });

  if (!state) {
    return (
      <p className="px-3 py-3 text-[12.5px] text-muted" data-testid="chat-availability-loading">
        {stateQ.isError ? t("webchat.agent.actionFailed") : t("common.loading")}
      </p>
    );
  }

  const push = (patch: Partial<{ available: boolean; topics: string[]; max_concurrent: number }>) =>
    save.mutate({
      available: state.available,
      topics: state.topics,
      max_concurrent: state.max_concurrent,
      ...patch,
    });

  const options = (topicsQ.data ?? []).map((tp) => ({ value: tp.ref, label: tp.label }));
  const commitMax = () => {
    const next = clampMax(Number(maxDraft));
    setMaxDraft(String(next));
    if (next !== state.max_concurrent) push({ max_concurrent: next });
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
          disabled={save.isPending}
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
          disabled={save.isPending}
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
          onChange={(e) => setMaxDraft(e.target.value)}
          onBlur={commitMax}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitMax();
          }}
          className="h-8 w-16 rounded border border-hairline bg-surface px-2 text-right text-[13px] tabular-nums text-ink"
        />
      </div>
      {error && (
        <p role="alert" className="text-[12px] text-danger" data-testid="chat-availability-error">
          {t("webchat.agent.actionFailed")}
        </p>
      )}
    </div>
  );
}
