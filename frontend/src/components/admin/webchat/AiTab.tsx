import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { chatAiStatusKey, webchatApi, type ChatAiStatus } from "@/lib/webchatApi";
import { setValues } from "@/lib/webchatForm";
import { Field, inputClass, type TabProps } from "./shared";

const KNOWN_REASONS = new Set([
  "ai_mode_off",
  "no_policy",
  "auto_reply_off",
  "no_service_user",
  "autonomy_off",
  "gate",
  "paused",
  "worker_down",
  "queue_rate_limit",
  "global_rate_limit",
  "budget_tokens_day",
  "llm_unavailable",
]);

/** A readable German/English sentence for a status code of `bot_availability`. */
function ReasonText({ status }: { status: ChatAiStatus }) {
  const { t } = useTranslation();
  const code = status.reason ?? "";
  return (
    <>
      {KNOWN_REASONS.has(code)
        ? t(`webchat.admin.ai.reasons.${code}`)
        : t("webchat.admin.ai.reasonUnknown", { code })}
    </>
  );
}

function StatusList({ configId }: { configId: number }) {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: chatAiStatusKey(configId),
    queryFn: ({ signal }) => webchatApi.aiStatus(configId, signal),
    staleTime: 10_000,
  });
  if (q.isLoading) {
    return <p className="text-xs text-muted">{t("webchat.admin.ai.statusLoading")}</p>;
  }
  if (q.isError || !q.data) {
    return (
      <p role="alert" className="text-xs text-danger" data-testid="webchat-ai-status-error">
        {t("webchat.admin.ai.statusFailed")}
      </p>
    );
  }
  return (
    <ul className="space-y-2" data-testid="webchat-ai-status">
      {q.data.map((s) => (
        <li
          key={s.topic_key}
          data-testid={`webchat-ai-status-${s.topic_key}`}
          className="rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-medium text-ink">{s.label}</span>
            <span
              data-testid={`webchat-ai-status-${s.topic_key}-state`}
              className={s.ok ? "text-xs font-medium text-ink" : "text-xs text-muted"}
            >
              {s.ok ? t("webchat.admin.ai.ok") : <ReasonText status={s} />}
            </span>
          </div>
          {s.queue_name && (
            <p className="mt-0.5 text-xs text-muted">{t("webchat.admin.ai.queue", { name: s.queue_name })}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Per topic "AI answers first", the assistant's name and tone, and a status list. */
export function AiTab({ draft, errors, onDraft, configId }: TabProps & { configId: number | null }) {
  const { t } = useTranslation();
  const v = draft.values;
  const setMode = (i: number, ai_mode: "off" | "first_line") =>
    onDraft(setValues(draft, { topics: v.topics.map((tp, j) => (j === i ? { ...tp, ai_mode } : tp)) }));

  return (
    <div className="space-y-6" data-testid="webchat-tab-ai">
      <p className="text-xs text-muted">{t("webchat.admin.ai.intro")}</p>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.ai.topicsTitle")}</h3>
        {v.topics.map((tp, i) => {
          const err = errors[`topics.${i}.ai_mode`];
          const name = tp.label.trim() || t("webchat.admin.ai.noTopicLabel");
          return (
            <div
              key={i}
              className="flex flex-wrap items-center justify-between gap-2"
              data-testid={`webchat-ai-topic-${i}`}
            >
              <span id={`webchat-ai-topic-${i}-label`} className="text-sm text-ink">
                {name}
              </span>
              <SegmentedControl
                testId={`webchat-ai-topic-${i}-mode`}
                aria-label={t("webchat.admin.ai.topicMode", { topic: name })}
                invalid={!!err}
                items={[
                  { value: "off", label: t("webchat.admin.ai.modeOff") },
                  { value: "first_line", label: t("webchat.admin.ai.modeOn") },
                ]}
                value={tp.ai_mode ?? "off"}
                onChange={(m) => setMode(i, m as "off" | "first_line")}
              />
              {err && (
                <p role="alert" className="w-full text-xs text-danger" data-testid={`webchat-ai-topic-${i}-error`}>
                  {err}
                </p>
              )}
            </div>
          );
        })}
      </section>

      <section className="grid gap-4">
        <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.ai.nameTitle")}</h3>
        <Field
          id="bot-name"
          label={t("webchat.admin.ai.botName")}
          hint={t("webchat.admin.ai.botNameHint")}
          error={errors.bot_name}
          className="max-w-md"
        >
          <input
            id="webchat-bot-name"
            data-testid="webchat-bot-name"
            className={inputClass}
            value={v.bot_name}
            maxLength={80}
            required
            aria-invalid={!!errors.bot_name}
            aria-describedby={errors.bot_name ? "webchat-bot-name-error" : undefined}
            onChange={(e) => onDraft(setValues(draft, { bot_name: e.target.value }))}
          />
        </Field>
        <Field
          id="bot-tone"
          label={t("webchat.admin.ai.botTone")}
          hint={t("webchat.admin.ai.botToneHint")}
          error={errors.bot_tone_prompt}
        >
          <textarea
            id="webchat-bot-tone"
            data-testid="webchat-bot-tone"
            className={inputClass}
            rows={3}
            maxLength={2000}
            value={v.bot_tone_prompt ?? ""}
            aria-invalid={!!errors.bot_tone_prompt}
            aria-describedby={errors.bot_tone_prompt ? "webchat-bot-tone-error" : undefined}
            onChange={(e) => onDraft(setValues(draft, { bot_tone_prompt: e.target.value }))}
          />
        </Field>
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.ai.statusTitle")}</h3>
          <Link
            to="/admin/ai/queues"
            data-testid="webchat-ai-queue-link"
            className="text-xs text-accent hover:underline"
          >
            {t("webchat.admin.ai.queueLink")}
          </Link>
        </div>
        {configId === null ? (
          <p
            data-testid="webchat-ai-status-unsaved"
            className="rounded-lg border border-dashed border-hairline p-3 text-xs text-muted"
          >
            {t("webchat.admin.ai.statusUnsaved")}
          </p>
        ) : (
          <>
            <StatusList configId={configId} />
            <p className="text-xs text-muted">{t("webchat.admin.ai.statusNote")}</p>
          </>
        )}
      </section>
    </div>
  );
}
