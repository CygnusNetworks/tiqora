import { useState, type ReactElement } from "react";
import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { changeAdminErrorText } from "./changeAdminUi";

/**
 * "Delete after asking" of the change administration lists: `ask(item)` opens the confirmation,
 * confirming runs `remove`, reloads `invalidate` and closes; a refused delete stays open with its
 * reason (`errorTestId`), cancelling forgets it. Render `dialog` once in the list.
 */
export function useDeleteConfirm<T>({
  remove,
  invalidate,
  title,
  message,
  errorTestId,
}: {
  remove: (item: T) => Promise<unknown>;
  invalidate: readonly QueryKey[];
  title: string;
  message: (item: T) => string;
  errorTestId: string;
}): { ask: (item: T) => void; dialog: ReactElement } {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [target, setTarget] = useState<{ item: T } | null>(null);
  const mutation = useMutation({
    mutationFn: remove,
    onSuccess: () => {
      setTarget(null);
      for (const queryKey of invalidate) void qc.invalidateQueries({ queryKey });
    },
  });
  const dialog = (
    <ConfirmDialog
      open={target !== null}
      title={title}
      message={
        <>
          <p>{target ? message(target.item) : ""}</p>
          {mutation.isError && (
            <p className="mt-2 text-danger" role="alert" data-testid={errorTestId}>
              {changeAdminErrorText(mutation.error, t)}
            </p>
          )}
        </>
      }
      variant="danger"
      confirmLabel={t("common.delete")}
      pending={mutation.isPending}
      onConfirm={() => target && mutation.mutate(target.item)}
      onCancel={() => {
        mutation.reset();
        setTarget(null);
      }}
    />
  );
  return { ask: (item: T) => setTarget({ item }), dialog };
}
