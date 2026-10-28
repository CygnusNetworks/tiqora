import { useState, type ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { approvalErrorCode, approvalErrorMessage, type ApprovalDelegation } from "@/lib/approvalsApi";
import { formatDateTime } from "@/lib/format";

/**
 * One substitute arrangement with its period and an end button, shared by the agent card and the admin page.
 * An already ended one answers 403 `delegation_not_active`: say so and reload instead of showing a failure.
 */
export function DelegationRow({
  d,
  title,
  titleTestId,
  stateLabel,
  prefix,
  revokeFn,
  disabled = false,
  onChanged,
}: {
  d: ApprovalDelegation;
  title: ReactNode;
  titleTestId: string;
  stateLabel: string;
  /** Test id prefix: `<prefix>-row`, `-period`, `-ended`, `-row-error`, `-revoke`. */
  prefix: string;
  revokeFn: (id: number) => Promise<unknown>;
  disabled?: boolean;
  onChanged: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revoke = useMutation({
    mutationFn: () => revokeFn(d.id),
    onSuccess: () => onChanged(),
    onError: (err) => {
      if (approvalErrorCode(err) === "delegation_not_active") {
        setEnded(true);
        setError(null);
        onChanged();
      } else {
        setError(approvalErrorMessage(err, t));
      }
    },
  });
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 px-3 py-2" data-testid={`${prefix}-row`} data-delegation-id={d.id}>
      <div className="min-w-0">
        <p className="text-sm font-medium text-ink" data-testid={titleTestId}>
          {title}
        </p>
        <p className="text-xs text-muted" data-testid={`${prefix}-period`}>
          {formatDateTime(d.valid_from, i18n.language)}
          {" – "}
          {d.valid_until ? formatDateTime(d.valid_until, i18n.language) : t("approvals.agent.delegation.noEnd")}
          {" · "}
          {stateLabel}
        </p>
        {ended && (
          <p className="text-xs text-muted" role="status" data-testid={`${prefix}-ended`}>
            {t("approvals.agent.delegation.alreadyEnded")}
          </p>
        )}
        {error && (
          <p className="text-xs text-danger" role="alert" data-testid={`${prefix}-row-error`}>
            {error}
          </p>
        )}
      </div>
      <Button size="sm" disabled={revoke.isPending || ended || disabled} onClick={() => revoke.mutate()} data-testid={`${prefix}-revoke`}>
        {t("approvals.agent.delegation.revoke")}
      </Button>
    </li>
  );
}
