import { useEffect, useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Switch } from "@/components/ui/Switch";
import { reportErrorMessage } from "@/lib/reportsApi";
import { savedSearchesApi, type SavedSearch, type SavedSearchIn } from "@/lib/savedSearchesApi";
import { SAVED_SEARCHES_KEY } from "./savedSearchQueries";
import { toTicketSortKey } from "./searchSort";

type ListParams = NonNullable<SavedSearchIn["list_params"]>;

/** What the queue list shows right now: its filter parameters plus its sort and page. */
export type SaveViewParams = Partial<ListParams> & {
  sort?: string;
  order?: "asc" | "desc";
  offset?: number;
  limit?: number;
};

function omitPage<T extends { offset?: number; limit?: number }>(params: T): Omit<T, "offset" | "limit"> {
  const copy = { ...params };
  delete copy.offset;
  delete copy.limit;
  return copy;
}

const inputCls =
  "w-full rounded border border-hairline bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent";

/** Saves the queue list's current view (filters and sort, not the page) as a saved search. */
export function SaveViewDialog({
  open,
  onClose,
  listParams,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  listParams: SaveViewParams;
  onSaved?: (saved: SavedSearch) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [sidebar, setSidebar] = useState(true);
  const [count, setCount] = useState(false);
  const [tried, setTried] = useState(false);

  const save = useMutation({
    mutationFn: () => {
      // Sort goes to its own fields; the page (offset, limit) is not part of a search.
      const { sort, order, ...rest } = listParams;
      const filter = omitPage(rest);
      const archived = filter.include_archived === true;
      return savedSearchesApi.create({
        name: name.trim(),
        list_params: { ...filter, include_archived: archived },
        sort_by: toTicketSortKey(sort),
        sort_order: order ?? "desc",
        include_archived: archived,
        show_in_sidebar: sidebar,
        show_count: sidebar && count,
      });
    },
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: SAVED_SEARCHES_KEY });
      onSaved?.(saved);
      onClose();
    },
  });

  useEffect(() => {
    if (!open) return;
    setName("");
    setSidebar(true);
    setCount(false);
    setTried(false);
    save.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the dialog opens
  }, [open]);

  const nameMissing = tried && name.trim() === "";
  const submit = () => {
    if (save.isPending) return;
    if (name.trim() === "") {
      setTried(true);
      return;
    }
    save.mutate();
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("savedSearches.saveView")}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} data-testid="save-view-cancel">
            {t("savedSearches.cancel")}
          </Button>
          <Button variant="primary" onClick={submit} disabled={save.isPending} data-testid="save-view-confirm">
            {save.isPending ? t("savedSearches.saving") : t("savedSearches.save")}
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        data-testid="save-view-dialog"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <p className="text-sm text-muted">{t("savedSearches.saveViewHint")}</p>
        <div className="space-y-1">
          <label htmlFor={`${id}-name`} className="block text-xs font-medium text-muted">
            {t("savedSearches.name")}
          </label>
          <input
            id={`${id}-name`}
            className={inputCls}
            value={name}
            maxLength={200}
            placeholder={t("savedSearches.namePlaceholder")}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={nameMissing || undefined}
            aria-describedby={nameMissing ? `${id}-name-error` : undefined}
            data-testid="save-view-name"
          />
          {nameMissing && (
            <p id={`${id}-name-error`} className="text-xs text-danger" data-testid="save-view-name-error">
              {t("savedSearches.nameRequired")}
            </p>
          )}
        </div>
        <div className="flex items-center justify-between gap-3">
          <label htmlFor={`${id}-sidebar`} className="text-sm text-ink">
            {t("savedSearches.showInSidebar")}
          </label>
          <Switch id={`${id}-sidebar`} checked={sidebar} onChange={setSidebar} testId="save-view-sidebar" />
        </div>
        <div className="flex items-center justify-between gap-3">
          <label htmlFor={`${id}-count`} className={sidebar ? "text-sm text-ink" : "text-sm text-muted"}>
            {t("savedSearches.showCount")}
          </label>
          <Switch id={`${id}-count`} checked={sidebar && count} onChange={setCount} disabled={!sidebar} testId="save-view-count" />
        </div>
        {save.isError && (
          <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-ink" role="alert" data-testid="save-view-error">
            {reportErrorMessage(save.error, t("savedSearches.saveFailed"))}
          </p>
        )}
      </form>
    </Dialog>
  );
}
