import { useEffect, useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { reportErrorMessage } from "@/lib/reportsApi";
import { savedSearchesApi, type TicketFilter, type ZnunyImportPreview } from "@/lib/savedSearchesApi";
import { SAVED_SEARCHES_KEY } from "./savedSearchQueries";

type Profile = ZnunyImportPreview["profiles"][number];

/** Number of single conditions in a filter tree (what the profile maps to). */
function countConditions(node: TicketFilter["where"] | undefined): number {
  if (!node) return 0;
  if ("field" in node) return 1;
  if ("all" in node) return node.all.reduce((n, c) => n + countConditions(c), 0);
  if ("any" in node) return node.any.reduce((n, c) => n + countConditions(c), 0);
  if ("not" in node) return countConditions(node.not);
  return 0;
}

function ProfileRow({
  profile,
  checked,
  onToggle,
}: {
  profile: Profile;
  checked: boolean;
  onToggle: (on: boolean) => void;
}) {
  const { t } = useTranslation();
  const uid = useId();
  const [showUnmapped, setShowUnmapped] = useState(false);
  const base = `znuny-import-row-${profile.name}`;
  const count = countConditions(profile.criteria.where);
  const hasUnmapped = profile.unmapped.length > 0;
  return (
    <li className="rounded-lg border border-hairline px-3 py-2.5" data-testid={base}>
      <div className="flex items-start gap-2.5">
        <input
          id={`${uid}-check`}
          type="checkbox"
          className="mt-0.5 h-4 w-4 shrink-0 accent-accent disabled:opacity-50"
          checked={checked}
          disabled={!profile.importable}
          onChange={(e) => onToggle(e.target.checked)}
          aria-describedby={`${uid}-info`}
          data-testid={`${base}-check`}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <label
              htmlFor={`${uid}-check`}
              className={`min-w-0 break-words text-sm font-medium ${profile.importable ? "text-ink" : "text-muted"}`}
            >
              {profile.name}
            </label>
            {profile.existing_id !== null && (
              <Badge tone="accent" data-testid={`${base}-update`}>
                {t("savedSearches.import.willUpdate")}
              </Badge>
            )}
            {profile.importable && profile.partial && (
              <Badge tone="warn" data-testid={`${base}-partial`}>
                {t("savedSearches.import.partial")}
              </Badge>
            )}
          </div>
          <p id={`${uid}-info`} className="mt-0.5 text-xs text-muted" data-testid={`${base}-summary`}>
            {profile.importable
              ? t("savedSearches.import.conditions", { count })
              : t("savedSearches.import.nothingMappable")}
          </p>
          {hasUnmapped && (
            <div className="mt-1.5">
              <button
                type="button"
                className="text-xs text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                aria-expanded={showUnmapped}
                aria-controls={`${uid}-unmapped`}
                onClick={() => setShowUnmapped((v) => !v)}
                data-testid={`${base}-unmapped-toggle`}
              >
                {t("savedSearches.import.unmapped", { count: profile.unmapped.length })}
              </button>
              {showUnmapped && (
                <ul id={`${uid}-unmapped`} className="mt-1.5 space-y-1.5" data-testid={`${base}-unmapped`}>
                  {profile.unmapped.map((u, i) => (
                    <li key={`${u.key}-${i}`} className="break-words rounded-md bg-surface-subtle px-2.5 py-1.5 text-xs">
                      <span className="font-mono text-ink">{u.key}</span>
                      {u.value !== "" && <span className="font-mono text-muted">{`: ${u.value}`}</span>}
                      <span className="mt-0.5 block text-muted">{u.reason}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

/** Takes over the person's own Znuny search profiles as saved searches; nothing is written to Znuny. */
export function ZnunyImportDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // Only what the person changed; everything else follows the default (complete profiles on, partial ones off).
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});

  const previewQ = useQuery({
    queryKey: ["znuny-import-preview"],
    queryFn: ({ signal }) => savedSearchesApi.znunyPreview(signal),
    enabled: open,
    staleTime: 0,
    gcTime: 0,
  });
  const apply = useMutation({
    mutationFn: (names: string[]) => savedSearchesApi.znunyImport(names),
    onSettled: () => queryClient.invalidateQueries({ queryKey: SAVED_SEARCHES_KEY }),
  });

  useEffect(() => {
    if (!open) return;
    setOverrides({});
    apply.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the dialog opens
  }, [open]);

  const profiles = previewQ.data?.profiles ?? [];
  const isChecked = (p: Profile) => p.importable && (overrides[p.name] ?? !p.partial);
  const selected = profiles.filter(isChecked).map((p) => p.name);
  const result = apply.data;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="xl"
      title={t("savedSearches.import.title")}
      description={result ? undefined : t("savedSearches.import.description")}
      footer={
        result ? (
          <Button variant="primary" onClick={onClose} data-testid="znuny-import-close">
            {t("savedSearches.import.close")}
          </Button>
        ) : (
          <>
            <Button variant="secondary" onClick={onClose} data-testid="znuny-import-cancel">
              {t("savedSearches.cancel")}
            </Button>
            {profiles.length > 0 && (
              <Button
                variant="primary"
                onClick={() => apply.mutate(selected)}
                disabled={selected.length === 0 || apply.isPending}
                data-testid="znuny-import-apply"
              >
                {apply.isPending
                  ? t("savedSearches.import.applying")
                  : t("savedSearches.import.apply", { count: selected.length })}
              </Button>
            )}
          </>
        )
      }
    >
      <div data-testid="znuny-import-dialog">
        {previewQ.isPending && (
          <p className="flex items-center gap-2 text-sm text-muted" data-testid="znuny-import-loading">
            <Spinner /> {t("savedSearches.import.loading")}
          </p>
        )}
        {previewQ.isError && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2">
            <p className="min-w-0 break-words text-sm text-ink" role="alert" data-testid="znuny-import-error">
              {reportErrorMessage(previewQ.error, t("savedSearches.import.loadFailed"))}
            </p>
            <Button size="sm" onClick={() => void previewQ.refetch()} disabled={previewQ.isFetching} data-testid="znuny-import-retry">
              {t("reports.retry")}
            </Button>
          </div>
        )}
        {previewQ.isSuccess && profiles.length === 0 && !result && (
          <p className="text-sm text-muted" data-testid="znuny-import-empty">
            {t("savedSearches.import.empty")}
          </p>
        )}
        {previewQ.isSuccess && profiles.length > 0 && !result && (
          <>
            <p className="mb-3 text-xs text-muted">{t("savedSearches.import.hint")}</p>
            <ul className="space-y-2" data-testid="znuny-import-list">
              {profiles.map((p) => (
                <ProfileRow
                  key={p.name}
                  profile={p}
                  checked={isChecked(p)}
                  onToggle={(on) => setOverrides((o) => ({ ...o, [p.name]: on }))}
                />
              ))}
            </ul>
          </>
        )}
        {apply.isError && (
          <p
            className="mt-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-ink"
            role="alert"
            data-testid="znuny-import-apply-error"
          >
            {reportErrorMessage(apply.error, t("savedSearches.import.applyFailed"))}
          </p>
        )}
        {result && (
          <div className="space-y-3 text-sm" role="status" data-testid="znuny-import-result">
            <p className="text-ink">
              {[
                result.created.length > 0 && t("savedSearches.import.created", { count: result.created.length }),
                result.updated.length > 0 && t("savedSearches.import.updated", { count: result.updated.length }),
                result.skipped.length > 0 && t("savedSearches.import.skipped", { count: result.skipped.length }),
              ]
                .filter(Boolean)
                .join(", ") || t("savedSearches.import.nothingDone")}
            </p>
            {result.skipped.length > 0 && (
              <ul className="space-y-1.5" data-testid="znuny-import-skipped">
                {result.skipped.map((s) => (
                  <li key={s.name} className="break-words rounded-md bg-surface-subtle px-2.5 py-1.5 text-xs">
                    <span className="font-medium text-ink">{s.name}</span>
                    <span className="mt-0.5 block text-muted">{s.reason}</span>
                  </li>
                ))}
              </ul>
            )}
            {result.created.length + result.updated.length > 0 && (
              <p className="text-xs text-muted">{t("savedSearches.import.whereToFind")}</p>
            )}
          </div>
        )}
      </div>
    </Dialog>
  );
}
