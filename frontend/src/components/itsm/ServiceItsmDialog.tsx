import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { itsmApi, itsmErrorCode, itsmKeys, type ServiceItsm } from "@/lib/itsmApi";

/** Znuny's default `ITSM::Service::Type` names (surveyed on 6.5 and 7.3): suggestions, free text allowed (D8). */
const SERVICE_TYPE_SUGGESTIONS = [
  "End User Service",
  "Front End",
  "Back End",
  "IT Management",
  "Reporting",
  "IT Operational",
  "Demonstration",
  "Project",
  "Training",
  "Underpinning Contract",
  "Other",
] as const;

/** The `ITSMCriticality` values the priority matrix uses. */
const CRITICALITY_SUGGESTIONS = ["1 very low", "2 low", "3 normal", "4 high", "5 very high"] as const;

type Draft = {
  service_type: string;
  criticality: string;
  description: string;
  portal_visible: boolean;
  min_minutes: string;
};

const draftOf = (s: ServiceItsm): Draft => ({
  service_type: s.service_type ?? "",
  criticality: s.criticality ?? "",
  description: s.description ?? "",
  portal_visible: s.portal_visible,
  min_minutes: s.min_minutes_between_incidents == null ? "" : String(s.min_minutes_between_incidents),
});

function Form({ service, onClose }: { service: ServiceItsm; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const listId = useId();
  const [draft, setDraft] = useState<Draft>(() => draftOf(service));
  const readOnlyKinds = service.znuny_owned;
  const minutes = draft.min_minutes.trim() === "" ? null : Number(draft.min_minutes);
  const minutesInvalid = minutes !== null && (!Number.isInteger(minutes) || minutes < 0);
  const save = useMutation({
    mutationFn: () =>
      itsmApi.admin.saveServiceMeta(service.service_id, {
        // Mode A: type and criticality are Znuny's; they are sent back unchanged (a change is refused).
        service_type: draft.service_type.trim() || null,
        criticality: draft.criticality.trim() || null,
        description: draft.description.trim() || null,
        portal_visible: draft.portal_visible,
        min_minutes_between_incidents: minutes,
        owner_user_id: service.owner_user_id,
      }),
    onSuccess: async (data) => {
      qc.setQueryData(itsmKeys.adminServiceMeta(service.service_id), data);
      await qc.invalidateQueries({ queryKey: itsmKeys.service(service.service_id) });
      onClose();
    },
  });
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((d) => ({ ...d, [key]: value }));
  const input = "mt-1 w-full rounded border border-hairline bg-surface px-2 py-1.5 text-ink disabled:opacity-60";

  return (
    <div className="space-y-3 text-sm" data-testid="service-itsm-form">
      {readOnlyKinds && (
        <p className="text-muted" data-testid="service-itsm-readonly-note">
          {t("admin.itsm.service.znunyOwned")}
        </p>
      )}
      <label className="block">
        <span className="text-xs text-muted">{t("admin.itsm.service.type")}</span>
        <input
          className={input}
          value={draft.service_type}
          disabled={readOnlyKinds}
          onChange={(e) => set("service_type", e.target.value)}
          list={`${listId}-type`}
          data-testid="service-itsm-type"
        />
        <datalist id={`${listId}-type`} data-testid="service-itsm-type-suggestions">
          {SERVICE_TYPE_SUGGESTIONS.map((v) => (
            <option key={v} value={v} />
          ))}
        </datalist>
      </label>
      <label className="block">
        <span className="text-xs text-muted">{t("admin.itsm.service.criticality")}</span>
        <input
          className={input}
          value={draft.criticality}
          disabled={readOnlyKinds}
          onChange={(e) => set("criticality", e.target.value)}
          list={`${listId}-criticality`}
          data-testid="service-itsm-criticality"
        />
        <datalist id={`${listId}-criticality`} data-testid="service-itsm-criticality-suggestions">
          {CRITICALITY_SUGGESTIONS.map((v) => (
            <option key={v} value={v} />
          ))}
        </datalist>
      </label>
      <label className="block">
        <span className="text-xs text-muted">{t("admin.itsm.service.description")}</span>
        <textarea
          className={input}
          rows={3}
          value={draft.description}
          onChange={(e) => set("description", e.target.value)}
          data-testid="service-itsm-description"
        />
      </label>
      {service.owner_name && (
        <p className="text-muted">{t("admin.itsm.service.owner", { name: service.owner_name })}</p>
      )}
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={draft.portal_visible}
          onChange={(e) => set("portal_visible", e.target.checked)}
          data-testid="service-itsm-portal"
        />
        {t("admin.itsm.service.portalVisible")}
      </label>
      <label className="block">
        <span className="text-xs text-muted">{t("admin.itsm.service.minMinutes")}</span>
        <input
          className={input}
          inputMode="numeric"
          value={draft.min_minutes}
          placeholder={String(service.effective_min_minutes_between_incidents)}
          onChange={(e) => set("min_minutes", e.target.value)}
          aria-invalid={minutesInvalid || undefined}
          data-testid="service-itsm-minutes"
        />
        <span className="text-xs text-muted">{t("admin.itsm.service.minMinutesHelp")}</span>
      </label>
      {save.isError && (
        <p role="alert" className="text-danger" data-testid="service-itsm-error">
          {t(`admin.itsm.service.errors.${itsmErrorCode(save.error) ?? "generic"}`, {
            defaultValue: t("admin.itsm.error"),
          })}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          {t("common.cancel")}
        </Button>
        <Button
          variant="primary"
          onClick={() => save.mutate()}
          disabled={save.isPending || minutesInvalid}
          data-testid="service-itsm-save"
        >
          {t("common.save")}
        </Button>
      </div>
    </div>
  );
}

/** The ITSM section of a service (type, criticality, owner, window between incidents). */
export function ServiceItsmDialog({
  serviceId,
  serviceName,
  onClose,
}: {
  serviceId: number;
  serviceName: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const meta = useQuery({
    queryKey: itsmKeys.adminServiceMeta(serviceId),
    queryFn: ({ signal }) => itsmApi.admin.serviceMeta(serviceId, signal),
  });
  return (
    <Dialog open onClose={onClose} title={t("admin.itsm.service.title", { name: serviceName })} size="lg">
      {meta.isPending && <Spinner />}
      {meta.isError && (
        <p role="alert" className="text-sm text-danger" data-testid="service-itsm-load-error">
          {t(`itsm.errors.${itsmErrorCode(meta.error) ?? "generic"}`, { defaultValue: t("admin.itsm.error") })}
        </p>
      )}
      {meta.data && <Form service={meta.data} onClose={onClose} />}
    </Dialog>
  );
}
