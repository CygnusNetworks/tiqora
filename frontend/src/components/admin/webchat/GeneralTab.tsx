import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { SelectField } from "@/components/ui/SelectField";
import { Switch } from "@/components/ui/Switch";
import { originLineNumbers, type ChatTopic, setValues } from "@/lib/webchatForm";
import { Field, inputClass, type TabProps } from "./shared";

export type RefOption<T extends string | number = number> = { value: T; label: string };

export function GeneralTab({
  draft,
  errors,
  onDraft,
  queues,
  priorities,
}: TabProps & { queues: RefOption[]; priorities: RefOption[] }) {
  const { t } = useTranslation();
  const v = draft.values;
  const lineNos = originLineNumbers(draft.originsText);
  const originErrors = Object.entries(errors)
    .filter(([k]) => k === "allowed_origins" || k.startsWith("allowed_origins."))
    .map(([k, msg]) => {
      const idx = k.split(".")[1];
      const line = idx === undefined ? null : (lineNos[Number(idx)] ?? Number(idx) + 1);
      return { key: k, line, msg };
    });

  const setTopics = (topics: ChatTopic[]) => onDraft(setValues(draft, { topics }));
  const patchTopic = (i: number, patch: Partial<ChatTopic>) =>
    setTopics(v.topics.map((tp, j) => (j === i ? { ...tp, ...patch } : tp)));
  const move = (i: number, by: -1 | 1) => {
    const to = i + by;
    if (to < 0 || to >= v.topics.length) return;
    const next = [...v.topics];
    [next[i], next[to]] = [next[to], next[i]];
    setTopics(next);
  };
  const noPriority = t("webchat.admin.general.priorityNone");

  return (
    <div className="space-y-5" data-testid="webchat-tab-general">
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
        <Field id="name" label={t("webchat.admin.general.name")} error={errors.name}>
          <input
            id="webchat-name"
            data-testid="webchat-name"
            className={inputClass}
            value={draft.name}
            aria-invalid={!!errors.name}
            maxLength={200}
            onChange={(e) => onDraft({ ...draft, name: e.target.value })}
          />
        </Field>
        <div className="flex items-end gap-2 pb-1.5">
          <Switch
            checked={draft.valid}
            onChange={(valid) => onDraft({ ...draft, valid })}
            testId="webchat-valid"
            aria-label={t("webchat.admin.general.active")}
          />
          <span className="text-sm text-ink">{t("webchat.admin.general.active")}</span>
        </div>
      </div>

      <div>
        <Field
          id="origins"
          label={t("webchat.admin.general.origins")}
          hint={t("webchat.admin.general.originsHint")}
        >
          <textarea
            id="webchat-origins"
            data-testid="webchat-origins"
            rows={4}
            spellCheck={false}
            placeholder="https://www.example.org"
            className={`${inputClass} font-mono`}
            value={draft.originsText}
            aria-invalid={originErrors.length > 0}
            aria-describedby={originErrors.length ? "webchat-origins-errors" : undefined}
            onChange={(e) => onDraft({ ...draft, originsText: e.target.value })}
          />
        </Field>
        {originErrors.length > 0 && (
          <ul id="webchat-origins-errors" data-testid="webchat-origins-errors" role="alert" className="mt-1 space-y-0.5 text-xs text-danger">
            {originErrors.map((e) => (
              <li key={e.key} data-testid={`webchat-origins-error-${e.line ?? "all"}`}>
                {e.line !== null ? t("webchat.admin.general.originLine", { line: e.line, message: e.msg }) : e.msg}
              </li>
            ))}
          </ul>
        )}
      </div>

      <section aria-labelledby="webchat-topics-title" className="space-y-2">
        <div>
          <h3 id="webchat-topics-title" className="text-sm font-semibold text-ink">
            {t("webchat.admin.general.topics")}
          </h3>
          <p className="text-xs text-muted">{t("webchat.admin.general.topicsHint")}</p>
        </div>
        {errors.topics && (
          <p role="alert" data-testid="webchat-topics-error" className="text-xs text-danger">
            {errors.topics}
          </p>
        )}
        {v.topics.map((tp, i) => (
          <div
            key={i}
            data-testid={`webchat-topic-${i}`}
            className="grid gap-2 rounded-md border border-hairline bg-surface-subtle/40 p-2 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_auto]"
          >
            <Field id={`topic-${i}-label`} label={t("webchat.admin.general.topicLabel")} error={errors[`topics.${i}.label`] ?? errors[`topics.${i}.key`]}>
              <input
                id={`webchat-topic-${i}-label`}
                data-testid={`webchat-topic-${i}-label`}
                className={inputClass}
                value={tp.label}
                aria-invalid={!!errors[`topics.${i}.label`]}
                onChange={(e) => patchTopic(i, { label: e.target.value })}
              />
            </Field>
            <Field id={`topic-${i}-queue`} label={t("webchat.admin.general.topicQueue")} error={errors[`topics.${i}.queue_id`]}>
              <SelectField
                id={`webchat-topic-${i}-queue`}
                testId={`webchat-topic-${i}-queue`}
                items={queues}
                value={tp.queue_id || null}
                placeholder={t("webchat.admin.general.pickQueue")}
                invalid={!!errors[`topics.${i}.queue_id`]}
                onChange={(queue_id) => patchTopic(i, { queue_id })}
              />
            </Field>
            <Field id={`topic-${i}-priority`} label={t("webchat.admin.general.topicPriority")} error={errors[`topics.${i}.priority_id`]}>
              <SelectField
                id={`webchat-topic-${i}-priority`}
                testId={`webchat-topic-${i}-priority`}
                items={[{ value: 0, label: noPriority }, ...priorities]}
                value={tp.priority_id ?? 0}
                onChange={(p) => patchTopic(i, { priority_id: p === 0 ? null : p })}
              />
            </Field>
            <div className="flex items-end gap-1 pb-0.5">
              <Button size="sm" variant="ghost" disabled={i === 0} aria-label={t("webchat.admin.general.moveUp")} data-testid={`webchat-topic-${i}-up`} onClick={() => move(i, -1)}>
                ↑
              </Button>
              <Button size="sm" variant="ghost" disabled={i === v.topics.length - 1} aria-label={t("webchat.admin.general.moveDown")} data-testid={`webchat-topic-${i}-down`} onClick={() => move(i, 1)}>
                ↓
              </Button>
              <Button size="sm" variant="ghost" disabled={v.topics.length <= 1} data-testid={`webchat-topic-${i}-remove`} onClick={() => setTopics(v.topics.filter((_, j) => j !== i))}>
                {t("webchat.admin.remove")}
              </Button>
            </div>
          </div>
        ))}
        <Button
          size="sm"
          data-testid="webchat-topic-add"
          disabled={v.topics.length >= 20}
          onClick={() => setTopics([...v.topics, { key: "", label: "", queue_id: 0, priority_id: null, ai_mode: "off" }])}
        >
          {t("webchat.admin.general.addTopic")}
        </Button>
      </section>

      <Field id="greeting" label={t("webchat.admin.general.greeting")} error={errors.greeting}>
        <textarea
          id="webchat-greeting"
          data-testid="webchat-greeting"
          rows={2}
          maxLength={500}
          className={inputClass}
          value={v.greeting ?? ""}
          aria-invalid={!!errors.greeting}
          onChange={(e) => onDraft(setValues(draft, { greeting: e.target.value }))}
        />
      </Field>
      <Field id="privacy" label={t("webchat.admin.general.privacy")} hint={t("webchat.admin.general.privacyHint")} error={errors.privacy_url}>
        <input
          id="webchat-privacy"
          data-testid="webchat-privacy"
          className={inputClass}
          placeholder="https://www.example.org/datenschutz"
          value={v.privacy_url ?? ""}
          aria-invalid={!!errors.privacy_url}
          onChange={(e) => onDraft(setValues(draft, { privacy_url: e.target.value }))}
        />
      </Field>
      <label className="flex items-center gap-2 text-sm text-ink">
        <Switch
          checked={v.show_agent_names}
          onChange={(show_agent_names) => onDraft(setValues(draft, { show_agent_names }))}
          testId="webchat-show-agent-names"
          aria-label={t("webchat.admin.general.showAgentNames")}
        />
        {t("webchat.admin.general.showAgentNames")}
      </label>
    </div>
  );
}
