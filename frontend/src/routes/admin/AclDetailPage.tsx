import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { api, ApiError, type AclUpdate } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { Button } from "@/components/ui/Button";

export function AclDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { aclId } = useParams({ from: "/admin/acl/$aclId" });
  const id = Number(aclId);

  const detailQ = useQuery({
    queryKey: ["admin", "acl", aclId],
    queryFn: ({ signal }) => api.getAcl(id, signal),
  });

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [comments, setComments] = useState("");
  const [validId, setValidId] = useState(1);
  const [stopAfterMatch, setStopAfterMatch] = useState(false);
  const [configMatch, setConfigMatch] = useState("");
  const [configChange, setConfigChange] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [savedFlash, setSavedFlash] = useState(false);

  useEffect(() => {
    const row = detailQ.data;
    if (!row) return;
    setName(row.name);
    setDescription(row.description ?? "");
    setComments(row.comments ?? "");
    setValidId(row.valid_id);
    setStopAfterMatch(Boolean(row.stop_after_match));
    setConfigMatch(row.config_match ?? "");
    setConfigChange(row.config_change ?? "");
  }, [detailQ.data]);

  const saveMut = useMutation({
    mutationFn: (body: AclUpdate) => api.updateAcl(id, body),
    onSuccess: async () => {
      setSubmitError(null);
      setSavedFlash(true);
      window.setTimeout(() => setSavedFlash(false), 1500);
      await qc.invalidateQueries({ queryKey: ["admin", "acl"] });
      await qc.invalidateQueries({ queryKey: ["admin", "acl", aclId] });
    },
    onError: (err: unknown) => {
      setSubmitError(err instanceof ApiError ? err.message : String(err));
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => api.deleteAcl(id),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["admin", "acl"] });
      void navigate({ to: "/admin/acl" });
    },
    onError: (err: unknown) => {
      setSubmitError(err instanceof ApiError ? err.message : String(err));
    },
  });

  const onSave = () => {
    setSubmitError(null);
    saveMut.mutate({
      name,
      description: description || null,
      comments: comments || null,
      valid_id: validId,
      stop_after_match: stopAfterMatch ? 1 : 0,
      config_match: configMatch || null,
      config_change: configChange || null,
    });
  };

  const onDelete = () => {
    if (!window.confirm(t("admin.acl.confirmDelete", { name: detailQ.data?.name ?? name }))) {
      return;
    }
    setSubmitError(null);
    deleteMut.mutate();
  };

  return (
    <div className="space-y-3 p-4" data-testid="admin-acl-detail-page">
      <Link to="/admin/acl" className="text-sm text-accent hover:underline">
        {t("common.back")}
      </Link>
      {detailQ.isLoading ? (
        <Spinner />
      ) : detailQ.data ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h1 className="font-display text-xl font-semibold text-ink">{detailQ.data.name}</h1>
            <div className="flex items-center gap-2">
              {savedFlash && (
                <span className="text-xs text-green" data-testid="acl-detail-saved">
                  {t("common.saved")}
                </span>
              )}
              <Button
                type="button"
                variant="danger"
                onClick={onDelete}
                disabled={deleteMut.isPending}
                data-testid="acl-detail-delete"
              >
                {t("common.delete")}
              </Button>
              <Button
                type="button"
                variant="primary"
                onClick={onSave}
                disabled={saveMut.isPending || !name.trim()}
                data-testid="acl-detail-save"
              >
                {t("common.save")}
              </Button>
            </div>
          </div>

          {submitError && (
            <p className="text-sm text-danger" data-testid="acl-detail-error">
              {submitError}
            </p>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block space-y-1">
              <span className="text-xs font-medium text-muted">{t("admin.acl.name")}</span>
              <input
                className="w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="acl-detail-name"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-xs font-medium text-muted">{t("admin.acl.description")}</span>
              <input
                className="w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                data-testid="acl-detail-description"
              />
            </label>
            <label className="block space-y-1 sm:col-span-2">
              <span className="text-xs font-medium text-muted">{t("admin.table.comments")}</span>
              <input
                className="w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
                value={comments}
                onChange={(e) => setComments(e.target.value)}
                data-testid="acl-detail-comments"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-xs font-medium text-muted">{t("admin.table.status")}</span>
              <select
                className="w-full rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
                value={validId}
                onChange={(e) => setValidId(Number(e.target.value))}
                data-testid="acl-detail-valid"
              >
                <option value={1}>{t("admin.table.valid")}</option>
                <option value={2}>{t("admin.table.invalid")}</option>
              </select>
            </label>
            <label className="flex items-center gap-2 pt-5 text-sm text-ink">
              <input
                type="checkbox"
                checked={stopAfterMatch}
                onChange={(e) => setStopAfterMatch(e.target.checked)}
                data-testid="acl-detail-stop-after-match"
              />
              <span className="flex items-center gap-1">
                {t("admin.acl.stopAfterMatch")}
                <HelpPopover title={t("admin.acl.stopAfterMatch")} testId="acl-detail-help-stop">
                  {t("admin.help.acl.stopAfterMatch")}
                </HelpPopover>
              </span>
            </label>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
                {t("admin.acl.match")}
                <HelpPopover title={t("admin.acl.match")} testId="acl-detail-help-match">
                  {t("admin.help.acl.match")}
                </HelpPopover>
              </div>
              <textarea
                className="min-h-[220px] w-full rounded-lg border border-hairline bg-surface p-3 font-mono text-xs text-ink"
                value={configMatch}
                onChange={(e) => setConfigMatch(e.target.value)}
                spellCheck={false}
                data-testid="acl-detail-config-match"
              />
            </div>
            <div className="space-y-1">
              <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
                {t("admin.acl.change")}
                <HelpPopover title={t("admin.acl.change")} testId="acl-detail-help-change">
                  {t("admin.help.acl.change")}
                </HelpPopover>
              </div>
              <textarea
                className="min-h-[220px] w-full rounded-lg border border-hairline bg-surface p-3 font-mono text-xs text-ink"
                value={configChange}
                onChange={(e) => setConfigChange(e.target.value)}
                spellCheck={false}
                data-testid="acl-detail-config-change"
              />
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
