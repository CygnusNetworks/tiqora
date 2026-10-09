import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import {
  identityApi,
  type IdentityProvider,
  type ProviderChanges,
  type TestAudience,
} from "@/lib/identityApi";
import { ssoErrorKey } from "@/lib/ssoErrors";
import { cn } from "@/lib/cn";
import { LoginPlanView } from "./LoginPlanView";

const POLL_MS = 1000;
const POLL_LIMIT_MS = 2 * 60 * 1000;

function show(value: unknown): string {
  if (Array.isArray(value)) return value.map(show).join(", ");
  if (value !== null && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "");
}

/** Starts a test sign-in (nothing is created or logged in) and shows what came back. */
export function TestTab({
  provider,
  testId,
  draft,
  onUseAttributes,
}: {
  provider: IdentityProvider;
  testId?: string;
  draft?: ProviderChanges;
  /** Hands the values of this test to the mapping tab's live preview. */
  onUseAttributes?: (attributes: Record<string, string[]>) => void;
}) {
  const { t } = useTranslation();
  const [audience, setAudience] = useState<TestAudience>(provider.audience === "customer" ? "customer" : "agent");
  const [timedOut, setTimedOut] = useState(false);
  const startedAt = useRef(Date.now());

  const startM = useMutation({
    mutationFn: () => identityApi.startTest(provider.id, { audience, draft }),
    onSuccess: (started) => window.location.assign(started.login_url),
  });

  const resultQ = useQuery({
    queryKey: ["admin", "identity", provider.id, "test", testId],
    enabled: !!testId,
    queryFn: ({ signal }) => identityApi.testResult(provider.id, testId as string, signal),
    refetchInterval: (query) => {
      if (query.state.status === "error") return false; // e.g. 404: do not keep asking
      if (query.state.data && query.state.data.status !== "started") return false;
      return Date.now() - startedAt.current > POLL_LIMIT_MS ? false : POLL_MS;
    },
  });

  useEffect(() => {
    if (!testId) return;
    startedAt.current = Date.now();
    setTimedOut(false);
    const timer = window.setTimeout(() => setTimedOut(true), POLL_LIMIT_MS);
    return () => window.clearTimeout(timer);
  }, [testId]);

  const result = resultQ.data;
  const hasDraft = !!draft && Object.keys(draft).length > 0;
  const errorKey = result?.status === "failed" ? ssoErrorKey(result.error ?? "unknown") : null;

  return (
    <div className="space-y-4" data-testid="identity-test-tab">
      <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <p className="text-sm text-muted">{t("admin.identity.test.intro")}</p>
        {provider.kind === "ldap" ? (
          <p data-testid="identity-test-ldap-hint" className="text-sm text-ink">
            {t("admin.identity.test.ldapHint")}
          </p>
        ) : (
          <>
            {provider.audience === "both" && (
              <div role="group" aria-label={t("admin.identity.test.audience")} className="flex items-center gap-1.5">
                {(["agent", "customer"] as const).map((a) => (
                  <button
                    key={a}
                    type="button"
                    data-testid={`identity-test-audience-${a}`}
                    aria-pressed={audience === a}
                    onClick={() => setAudience(a)}
                    className={cn(
                      "rounded-lg border px-3 py-1.5 text-[12.5px] font-medium",
                      audience === a
                        ? "border-accent bg-accent text-accent-ink"
                        : "border-hairline bg-surface text-muted hover:text-ink",
                    )}
                  >
                    {t(`admin.identity.test.audiences.${a}`)}
                  </button>
                ))}
              </div>
            )}
            {hasDraft && <p className="text-xs text-muted">{t("admin.identity.test.draftHint")}</p>}
            <div className="flex items-center gap-3">
              <Button
                type="button"
                variant="primary"
                data-testid="identity-test-start"
                disabled={startM.isPending}
                onClick={() => startM.mutate()}
              >
                {t("admin.identity.test.start")}
              </Button>
              {startM.isError && (
                <span role="alert" data-testid="identity-test-start-error" className="text-sm text-danger">
                  {apiErrorMessage(startM.error, t("admin.identity.test.startError"))}
                </span>
              )}
            </div>
          </>
        )}
      </section>

      {testId && (
        <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="identity-test-result">
          <h2 className="text-sm font-semibold text-ink">{t("admin.identity.test.resultHeading")}</h2>
          {resultQ.isError && (
            <p role="alert" className="text-sm text-danger">
              {apiErrorMessage(resultQ.error, t("admin.identity.test.loadError"))}
            </p>
          )}
          {(!result || result.status === "started") && !resultQ.isError && (
            <div className="flex items-center gap-2 text-sm text-muted" data-testid="identity-test-waiting">
              {timedOut ? (
                <span>{t("admin.identity.test.timeout")}</span>
              ) : (
                <>
                  <Spinner />
                  <span>{t("admin.identity.test.waiting")}</span>
                </>
              )}
            </div>
          )}
          {errorKey && (
            <p
              data-testid="identity-test-error"
              role="alert"
              className="rounded border border-danger/30 bg-danger/15 px-3 py-2 text-sm text-ink"
            >
              {t(errorKey)}
            </p>
          )}
          {result?.status === "done" && (
            <>
              {result.subject && (
                <p className="text-sm text-ink">
                  <span className="text-muted">{t("admin.identity.test.subject")}: </span>
                  <code>{result.subject}</code>
                </p>
              )}
              <div>
                <h3 className="mb-1 text-xs font-medium text-muted">{t("admin.identity.test.attributesHeading")}</h3>
                <table data-testid="identity-test-attributes" className="w-full text-left text-sm">
                  <tbody>
                    {Object.entries(result.attributes).map(([name, value]) => (
                      <tr key={name} className="border-t border-hairline">
                        <th scope="row" className="py-1 pr-3 align-top font-mono text-[12.5px] font-normal text-muted">
                          {name}
                        </th>
                        <td className="break-all py-1 text-ink">{show(value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {onUseAttributes && (
                <Button
                  type="button"
                  data-testid="identity-test-use-sample"
                  onClick={() => onUseAttributes(result.attributes)}
                >
                  {t("admin.identity.test.useForPreview")}
                </Button>
              )}
              {result.plan && <LoginPlanView plan={result.plan} />}
            </>
          )}
        </section>
      )}
    </div>
  );
}
