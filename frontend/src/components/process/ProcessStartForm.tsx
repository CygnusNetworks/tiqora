import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { processApi } from "@/lib/processApi";
import { Spinner } from "@/components/ui/Spinner";
import { processStartErrorMessage } from "./processStartError";
import { ProcessFormBody } from "./ProcessFormBody";

/**
 * The start dialog of one process: loads the (permission/ACL filtered) start form, validates and hands
 * the submit-shaped field values to `onSubmit`. Used by the agent "new process ticket" page and by the
 * enroll dialog (`ticketId` set), so both render and submit the same way.
 */
export function ProcessStartForm({
  processEntityId,
  ticketId,
  onSubmit,
  pending,
  error,
  onCancel,
  testIdPrefix,
  api = processApi,
}: {
  processEntityId: string;
  ticketId?: number;
  onSubmit: (fieldValues: Record<string, unknown>) => void;
  pending: boolean;
  /** Message of the failed submit, shown above the button. */
  error?: string | null;
  onCancel?: () => void;
  testIdPrefix: string;
  /** Agent (`processApi`, default) or customer portal (`portalProcessApi`) start-form source. */
  api?: Pick<typeof processApi, "startForm">;
}) {
  const { t } = useTranslation();

  const formQ = useQuery({
    queryKey: [api === processApi ? "process" : "portal-process", "start-form", processEntityId, ticketId ?? null],
    queryFn: ({ signal }) => api.startForm(processEntityId, { ticketId }, signal),
    // options are ACL-filtered per open; never show a stale form
    gcTime: 0,
    retry: false,
  });
  const form = formQ.data;

  if (formQ.isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }
  if (formQ.isError || !form) {
    return (
      <p className="text-sm text-danger" role="alert" data-testid={`${testIdPrefix}-load-error`}>
        {processStartErrorMessage(formQ.error, t, "process.form.loadError")}
      </p>
    );
  }

  return (
    <ProcessFormBody
      key={processEntityId}
      form={form}
      onSubmit={onSubmit}
      pending={pending}
      error={error}
      onCancel={onCancel}
      testIdPrefix={testIdPrefix}
    />
  );
}
