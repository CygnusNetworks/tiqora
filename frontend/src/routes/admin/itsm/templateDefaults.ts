/** Pure helpers of the change template defaults (`TemplateDefaults`). */
import type { AdminRiskQuestion, ChangeTemplateDefaults } from "@/lib/changeAdminApi";

/** Every field of `TemplateDefaults` present (null / empty when not set), so the save body is complete. */
export type Defaults = Required<ChangeTemplateDefaults>;

export function normalizeDefaults(d: ChangeTemplateDefaults | undefined): Defaults {
  return {
    title: d?.title ?? null,
    queue_id: d?.queue_id ?? null,
    priority_id: d?.priority_id ?? null,
    service_ids: d?.service_ids ?? [],
    cab_id: d?.cab_id ?? null,
    reason: d?.reason ?? null,
    implementation_plan: d?.implementation_plan ?? null,
    backout_plan: d?.backout_plan ?? null,
    test_plan: d?.test_plan ?? null,
    risk_answers: d?.risk_answers ?? {},
    duration_minutes: d?.duration_minutes ?? null,
    portal_visible: d?.portal_visible ?? false,
  };
}

/**
 * The stored answers that still fit a valid question and one of its answers (the backend refuses
 * others on save); `questions` undefined (not loaded) keeps them all.
 */
export function fittingRiskAnswers(answers: Record<string, string>, questions: AdminRiskQuestion[] | undefined): Record<string, string> {
  if (!questions) return answers;
  return Object.fromEntries(
    Object.entries(answers).filter(([q, a]) => questions.some((x) => x.valid && x.question_key === q && x.answers.some((y) => y.key === a))),
  );
}
