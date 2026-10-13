import { useTranslation } from "react-i18next";
import type { IdentityTestMfa, LoginPlan } from "@/lib/identityApi";

function NameList({ testId, title, items }: { testId: string; title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div data-testid={testId}>
      <div className="text-xs font-medium text-muted">{title}</div>
      <ul className="list-disc pl-5 text-sm text-ink">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

/** What a sign-in with the received values would do: account, creation, rejection, role changes. */
export function LoginPlanView({ plan, mfa }: { plan: LoginPlan; mfa?: IdentityTestMfa | null }) {
  const { t } = useTranslation();
  const changes = Object.entries(plan.profile_changes);
  return (
    <div className="space-y-3" data-testid="identity-plan">
      {plan.decision === "reject" ? (
        <p
          data-testid="identity-plan-reject"
          role="alert"
          className="rounded border border-danger/30 bg-danger/15 px-3 py-2 text-sm text-ink"
        >
          <span className="font-medium">{t("admin.identity.plan.rejected")}</span>{" "}
          {plan.reject_message ?? plan.reject_code}
        </p>
      ) : plan.decision === "create" ? (
        <div data-testid="identity-plan-create" className="text-sm text-ink">
          <p className="font-medium">{t("admin.identity.plan.create")}</p>
          {plan.login && <p className="text-muted">{t("admin.identity.plan.newLogin", { login: plan.login })}</p>}
        </div>
      ) : (
        <div data-testid="identity-plan-login" className="text-sm text-ink">
          <p className="font-medium">{t("admin.identity.plan.loginAs", { login: plan.login })}</p>
          {plan.matched_by && (
            <p className="text-muted">{t(`admin.identity.plan.matchedBy.${plan.matched_by}`)}</p>
          )}
        </div>
      )}
      {mfa && (
        <p data-testid="identity-plan-mfa" className="text-sm text-ink">
          <span className="text-muted">{t("admin.identity.test.mfa.label")}: </span>
          <span className="font-medium">
            {t(`admin.identity.test.mfa.${!mfa.required ? "notRequired" : mfa.satisfied ? "satisfied" : "unsatisfied"}`)}
          </span>
        </p>
      )}
      {changes.length > 0 && (
        <div data-testid="identity-plan-profile">
          <div className="text-xs font-medium text-muted">{t("admin.identity.plan.profileChanges")}</div>
          <ul className="text-sm text-ink">
            {changes.map(([field, [from, to]]) => (
              <li key={field}>
                {field}: {from || "–"} → {to || "–"}
              </li>
            ))}
          </ul>
        </div>
      )}
      <NameList
        testId="identity-plan-roles-add"
        title={t("admin.identity.plan.rolesAdd")}
        items={plan.roles_add.map((r) => r.name)}
      />
      <NameList
        testId="identity-plan-roles-remove"
        title={t("admin.identity.plan.rolesRemove")}
        items={plan.roles_remove.map((r) => r.name)}
      />
      <NameList
        testId="identity-plan-groups-add"
        title={t("admin.identity.plan.groupsAdd")}
        items={plan.groups_add.map((g) => `${g.group_name} (${g.permission_key})`)}
      />
      <NameList
        testId="identity-plan-groups-remove"
        title={t("admin.identity.plan.groupsRemove")}
        items={plan.groups_remove.map((g) => `${g.group_name} (${g.permission_key})`)}
      />
    </div>
  );
}
