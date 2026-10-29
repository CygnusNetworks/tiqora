import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Switch } from "@/components/ui/Switch";
import { changeApi, type Change } from "@/lib/changeApi";
import { changeErrorText, FINAL_PHASES, useChangeRefresh } from "./changeUi";

/**
 * "Im Kundenportal anzeigen": whether the change's window shows in the portal's maintenance
 * schedule (`portal_visible`, PATCH). Writers switch it on an open change; others see the state.
 */
export function PortalVisibleSwitch({ change }: { change: Change }) {
  const { t } = useTranslation();
  const refresh = useChangeRefresh(change.ticket_id);
  const save = useMutation({
    mutationFn: (visible: boolean) => changeApi.update(change.ticket_id, { portal_visible: visible }),
    onSuccess: (c) => refresh(c),
  });
  const editable = change.can_write && !FINAL_PHASES.has(change.phase);
  return (
    <div className="space-y-1 text-xs" data-testid="change-portal-visible">
      <div className="flex items-center gap-2">
        <Switch
          id="change-portal-visible-switch"
          testId="change-portal-visible-switch"
          checked={save.isPending ? !!save.variables : change.portal_visible}
          disabled={!editable || save.isPending}
          onChange={(v) => save.mutate(v)}
          aria-describedby="change-portal-visible-help"
        />
        <label htmlFor="change-portal-visible-switch" className="text-ink">
          {t("itsm.change.portal.label")}
        </label>
      </div>
      <p id="change-portal-visible-help" className="text-muted">
        {t("itsm.change.portal.help")}
      </p>
      {save.isError && (
        <p className="text-danger" role="alert" data-testid="change-portal-visible-error">
          {changeErrorText(save.error, t)}
        </p>
      )}
    </div>
  );
}
