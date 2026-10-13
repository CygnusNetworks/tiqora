import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { apiErrorMessage } from "@/lib/avatarApi";
import { formatDateTime } from "@/lib/format";
import { identityApi, type ScimEvent } from "@/lib/identityApi";
import { toBcp47 } from "@/i18n";

const PAGE = 50;

/** Protocol of the requests the identity provider sent, newest first. */
export function ScimEventsList({ providerId }: { providerId: number }) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const [failuresOnly, setFailuresOnly] = useState(false);

  const q = useInfiniteQuery({
    queryKey: ["identity", providerId, "scim", "events", failuresOnly],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      identityApi.listScimEvents(
        providerId,
        { before_id: pageParam, limit: PAGE, failures_only: failuresOnly },
        signal,
      ),
    getNextPageParam: (last: ScimEvent[]) => (last.length >= PAGE ? last[last.length - 1].id : undefined),
  });
  const events = q.data?.pages.flat() ?? [];

  return (
    <section className="space-y-3 rounded-lg border border-hairline bg-surface p-4" data-testid="scim-events">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">{t("admin.identity.scim.events.title")}</h2>
        <label className="flex items-center gap-2 text-xs text-ink">
          <input
            type="checkbox"
            data-testid="scim-events-failures"
            checked={failuresOnly}
            onChange={(e) => setFailuresOnly(e.target.checked)}
          />
          {t("admin.identity.scim.events.failuresOnly")}
        </label>
      </div>
      {q.isLoading && <Spinner />}
      {q.isError && (
        <p role="alert" className="text-sm text-danger">
          {apiErrorMessage(q.error, t("admin.identity.scim.events.loadError"))}
        </p>
      )}
      {q.data && events.length === 0 && (
        <p data-testid="scim-events-empty" className="text-sm text-muted">
          {t("admin.identity.scim.events.empty")}
        </p>
      )}
      {events.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="px-3 py-2 font-medium">{t("admin.identity.scim.events.time")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.scim.events.request")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.scim.events.operationLabel")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.scim.accounts.login")}</th>
                <th className="px-3 py-2 font-medium">{t("admin.identity.scim.events.status")}</th>
              </tr>
            </thead>
            <tbody>
              {events.map((ev) => (
                <tr key={ev.id} className="border-t border-hairline" data-testid="scim-event-row">
                  <td className="whitespace-nowrap px-3 py-2 text-muted">{formatDateTime(ev.created, locale)}</td>
                  <td className="px-3 py-2 font-mono text-[12.5px] text-muted">
                    {ev.method} {ev.resource}
                  </td>
                  <td className="px-3 py-2 text-ink">
                    {t(`admin.identity.scim.events.operation.${ev.operation}`, { defaultValue: ev.operation })}
                    {ev.detail && <span className="ml-2 text-xs text-muted">{ev.detail}</span>}
                  </td>
                  <td className="px-3 py-2 text-ink">{ev.login ?? "—"}</td>
                  <td className="px-3 py-2">
                    <Badge tone={ev.status >= 400 ? "danger" : "success"}>{ev.status}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {q.hasNextPage && (
        <Button
          type="button"
          data-testid="scim-events-more"
          disabled={q.isFetchingNextPage}
          onClick={() => q.fetchNextPage()}
        >
          {t("admin.identity.scim.events.more")}
        </Button>
      )}
    </section>
  );
}
