import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";

export type Id = number | string;

export interface AssignmentSide<Item> {
  /** Discriminator for query keys + i18n side noun. */
  key: string;
  /** i18n key for the side label (e.g. "admin.queueTemplates.queue"). */
  labelKey: string;
  loadItems: (signal?: AbortSignal) => Promise<Item[]>;
  getId: (item: Item) => Id;
  getLabel: (item: Item) => string;
  getSubLabel?: (item: Item) => string | undefined;
}

export interface AssignmentConfig<A, B> {
  testId: string;
  titleKey: string;
  subtitleKey: string;
  sideA: AssignmentSide<A>;
  sideB: AssignmentSide<B>;
  /** Counterparts (side B) currently assigned to a side-A anchor. */
  loadAssignedB: (aId: Id, signal?: AbortSignal) => Promise<B[]>;
  /** Reverse: side-A items assigned to a side-B anchor. */
  loadAssignedA: (bId: Id, signal?: AbortSignal) => Promise<A[]>;
  /**
   * Identity-symmetric write: always (sideA-id, sideB-id) regardless of which
   * side is currently the master (anchor).
   */
  assign: (aId: Id, bId: Id) => Promise<void>;
  revoke: (aId: Id, bId: Id) => Promise<void>;
}

type Direction = "a" | "b";

function idKey(id: Id): string {
  return String(id);
}

/**
 * Reusable master-detail relation editor with a direction toggle
 * (design #5 — "Master-Detail mit Richtungs-Umschalter").
 *
 * Selecting an anchor loads its assigned counterparts (preselection). Switching
 * direction flips which side is the master list vs. the checklist. Count badges
 * on master rows only appear when that anchor's assignment set is already in the
 * react-query cache (no N+1 up-front).
 */
export function AssignmentEditor<A, B>({ config }: { config: AssignmentConfig<A, B> }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [direction, setDirection] = useState<Direction>("a");
  const [selectedId, setSelectedId] = useState<Id | null>(null);
  const [anchorFilter, setAnchorFilter] = useState("");
  const [counterpartFilter, setCounterpartFilter] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);

  // Runtime side adapters — both sides are treated as opaque items; getId/getLabel
  // close over the correct Side<A|B> via the direction branch.
  const anchorSide = (
    direction === "a" ? config.sideA : config.sideB
  ) as AssignmentSide<unknown>;
  const counterpartSide = (
    direction === "a" ? config.sideB : config.sideA
  ) as AssignmentSide<unknown>;

  const itemsKey = useCallback(
    (sideKey: string) => ["admin", "assignment", config.testId, "items", sideKey] as const,
    [config.testId],
  );

  const assignedKey = useCallback(
    (dir: Direction, anchorId: Id) =>
      ["admin", "assignment", config.testId, "assigned", dir, idKey(anchorId)] as const,
    [config.testId],
  );

  const anchorsQ = useQuery<unknown[]>({
    queryKey: itemsKey(anchorSide.key),
    queryFn: ({ signal }) => anchorSide.loadItems(signal),
    staleTime: 5 * 60 * 1000,
  });

  const counterpartsQ = useQuery<unknown[]>({
    queryKey: itemsKey(counterpartSide.key),
    queryFn: ({ signal }) => counterpartSide.loadItems(signal),
    staleTime: 5 * 60 * 1000,
  });

  const assignedQ = useQuery<unknown[]>({
    queryKey:
      selectedId !== null
        ? assignedKey(direction, selectedId)
        : (["admin", "assignment", config.testId, "idle"] as const),
    queryFn: async ({ signal }) => {
      if (selectedId === null) return [];
      if (direction === "a") return config.loadAssignedB(selectedId, signal);
      return config.loadAssignedA(selectedId, signal);
    },
    enabled: selectedId !== null,
  });

  const assignedIdSet = useMemo(() => {
    const set = new Set<string>();
    for (const item of assignedQ.data ?? []) {
      set.add(idKey(counterpartSide.getId(item)));
    }
    return set;
  }, [assignedQ.data, counterpartSide]);

  const selectedAnchorLabel = useMemo(() => {
    if (selectedId === null) return "";
    const found = (anchorsQ.data ?? []).find(
      (item) => idKey(anchorSide.getId(item)) === idKey(selectedId),
    );
    return found !== undefined ? anchorSide.getLabel(found) : idKey(selectedId);
  }, [selectedId, anchorsQ.data, anchorSide]);

  const flashSaved = useCallback(() => {
    setSavedFlash(true);
    window.setTimeout(() => setSavedFlash(false), 1400);
  }, []);

  const writePair = useCallback(
    async (counterpartId: Id, next: boolean) => {
      if (selectedId === null) return;
      if (direction === "a") {
        if (next) await config.assign(selectedId, counterpartId);
        else await config.revoke(selectedId, counterpartId);
      } else {
        if (next) await config.assign(counterpartId, selectedId);
        else await config.revoke(counterpartId, selectedId);
      }
    },
    [config, direction, selectedId],
  );

  const toggleM = useMutation({
    mutationFn: async ({
      counterpartId,
      next,
    }: {
      counterpartId: Id;
      next: boolean;
    }) => {
      await writePair(counterpartId, next);
    },
    onMutate: async ({ counterpartId, next }) => {
      if (selectedId === null) return { previous: undefined as unknown };
      const key = assignedKey(direction, selectedId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData(key);
      const counterparts = counterpartsQ.data ?? [];
      queryClient.setQueryData(key, (old: unknown[] | undefined) => {
        const list = Array.isArray(old) ? [...old] : [];
        const match = (item: unknown) =>
          idKey(counterpartSide.getId(item)) === idKey(counterpartId);
        if (next) {
          if (list.some(match)) return list;
          const item = counterparts.find(
            (c) => idKey(counterpartSide.getId(c)) === idKey(counterpartId),
          );
          return item !== undefined ? [...list, item] : list;
        }
        return list.filter((item) => !match(item));
      });
      return { previous, key };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.key && ctx.previous !== undefined) {
        queryClient.setQueryData(ctx.key, ctx.previous);
      }
    },
    onSuccess: () => {
      flashSaved();
    },
    onSettled: () => {
      if (selectedId === null) return;
      void queryClient.invalidateQueries({
        queryKey: assignedKey(direction, selectedId),
      });
    },
  });

  const bulkM = useMutation({
    mutationFn: async ({
      mode,
      targetIds,
    }: {
      mode: "all" | "none";
      /** Snapshot of counterpart ids to assign (all) or revoke (none). */
      targetIds: Id[];
    }) => {
      if (selectedId === null) return;
      await Promise.all(
        targetIds.map((cId) => writePair(cId, mode === "all")),
      );
    },
    onMutate: async ({ mode }) => {
      if (selectedId === null) return { previous: undefined as unknown };
      const key = assignedKey(direction, selectedId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData(key);
      if (mode === "all") {
        queryClient.setQueryData(key, counterpartsQ.data ?? []);
      } else {
        queryClient.setQueryData(key, []);
      }
      return { previous, key };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.key && ctx.previous !== undefined) {
        queryClient.setQueryData(ctx.key, ctx.previous);
      }
    },
    onSuccess: () => {
      flashSaved();
    },
    onSettled: () => {
      if (selectedId === null) return;
      void queryClient.invalidateQueries({
        queryKey: assignedKey(direction, selectedId),
      });
    },
  });

  const runBulkAll = () => {
    if (selectedId === null) return;
    const targetIds = (counterpartsQ.data ?? [])
      .map((item) => counterpartSide.getId(item))
      .filter((cId) => !assignedIdSet.has(idKey(cId)));
    if (targetIds.length === 0) return;
    bulkM.mutate({ mode: "all", targetIds });
  };

  const runBulkNone = () => {
    if (selectedId === null) return;
    const targetIds = (counterpartsQ.data ?? [])
      .map((item) => counterpartSide.getId(item))
      .filter((cId) => assignedIdSet.has(idKey(cId)));
    if (targetIds.length === 0) return;
    bulkM.mutate({ mode: "none", targetIds });
  };

  const switchDirection = (next: Direction) => {
    if (next === direction) return;
    setDirection(next);
    setSelectedId(null);
    setAnchorFilter("");
    setCounterpartFilter("");
    setSavedFlash(false);
  };

  const filteredAnchors = useMemo(() => {
    const anchors = anchorsQ.data ?? [];
    const q = anchorFilter.trim().toLowerCase();
    if (!q) return anchors;
    return anchors.filter((item) => {
      const label = anchorSide.getLabel(item).toLowerCase();
      const sub = anchorSide.getSubLabel?.(item)?.toLowerCase() ?? "";
      return label.includes(q) || sub.includes(q);
    });
  }, [anchorsQ.data, anchorFilter, anchorSide]);

  const filteredCounterparts = useMemo(() => {
    const list = counterpartsQ.data ?? [];
    const q = counterpartFilter.trim().toLowerCase();
    if (!q) return list;
    return list.filter((item) => {
      const label = counterpartSide.getLabel(item).toLowerCase();
      const sub = counterpartSide.getSubLabel?.(item)?.toLowerCase() ?? "";
      return label.includes(q) || sub.includes(q);
    });
  }, [counterpartsQ.data, counterpartFilter, counterpartSide]);

  const counterparts = counterpartsQ.data ?? [];

  /** Count only when that anchor's assignment set is already cached (no N+1). */
  const cachedCount = (anchorId: Id): number | null => {
    const data = queryClient.getQueryData(assignedKey(direction, anchorId));
    if (Array.isArray(data)) return data.length;
    if (selectedId !== null && idKey(selectedId) === idKey(anchorId) && assignedQ.data) {
      return assignedQ.data.length;
    }
    return null;
  };

  const pending = toggleM.isPending || bulkM.isPending;
  const busy = pending || assignedQ.isFetching || anchorsQ.isLoading || counterpartsQ.isLoading;

  return (
    <div className="space-y-4 p-4" data-testid={config.testId}>
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">{t(config.titleKey)}</h1>
        <p className="mt-1 text-sm text-muted">{t(config.subtitleKey)}</p>
      </div>

      {/* Direction toggle */}
      <div
        role="tablist"
        aria-label={t("admin.assignmentEditor.directionBy", {
          side: t(config.sideA.labelKey),
        })}
        className="inline-flex max-w-full flex-wrap rounded-lg border border-hairline bg-surface p-0.5"
      >
        <button
          type="button"
          role="tab"
          aria-selected={direction === "a"}
          data-testid={`${config.testId}-direction-a`}
          onClick={() => switchDirection("a")}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
            direction === "a"
              ? "bg-accent text-accent-ink"
              : "text-muted hover:text-ink",
          )}
        >
          {t("admin.assignmentEditor.directionBy", { side: t(config.sideA.labelKey) })}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={direction === "b"}
          data-testid={`${config.testId}-direction-b`}
          onClick={() => switchDirection("b")}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
            direction === "b"
              ? "bg-accent text-accent-ink"
              : "text-muted hover:text-ink",
          )}
        >
          {t("admin.assignmentEditor.directionBy", { side: t(config.sideB.labelKey) })}
        </button>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {/* Master column */}
        <section
          className="flex min-h-[20rem] flex-col rounded-lg border border-hairline bg-surface"
          aria-label={t(anchorSide.labelKey)}
        >
          <div className="border-b border-hairline px-3 py-2">
            <label className="block">
              <span className="sr-only">{t("admin.assignmentEditor.searchAnchor")}</span>
              <input
                type="search"
                value={anchorFilter}
                onChange={(e) => setAnchorFilter(e.currentTarget.value)}
                placeholder={t("admin.assignmentEditor.searchAnchor")}
                data-testid={`${config.testId}-search-anchor`}
                className="w-full rounded-md border border-hairline bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              />
            </label>
          </div>
          <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-hairline">
            {anchorsQ.isLoading && (
              <li className="flex justify-center px-3 py-8">
                <Spinner className="size-5" />
              </li>
            )}
            {!anchorsQ.isLoading && filteredAnchors.length === 0 && (
              <li className="px-3 py-8 text-center text-sm text-muted">
                {t("admin.assignmentEditor.noAnchors")}
              </li>
            )}
            {filteredAnchors.map((item) => {
              const id = anchorSide.getId(item);
              const selected = selectedId !== null && idKey(selectedId) === idKey(id);
              const count = cachedCount(id);
              const sub = anchorSide.getSubLabel?.(item);
              return (
                <li key={idKey(id)}>
                  <button
                    type="button"
                    data-testid={`${config.testId}-anchor-${idKey(id)}`}
                    onClick={() => setSelectedId(id)}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent",
                      selected
                        ? "bg-accent-dim text-accent"
                        : "text-ink hover:bg-surface-subtle",
                    )}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {anchorSide.getLabel(item)}
                      </span>
                      {sub ? (
                        <span
                          className={cn(
                            "block truncate text-xs",
                            selected ? "text-accent/80" : "text-muted",
                          )}
                        >
                          {sub}
                        </span>
                      ) : null}
                    </span>
                    <span
                      className={cn(
                        "shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[11px] tabular-nums",
                        selected
                          ? "bg-accent/20 text-accent"
                          : "bg-surface-subtle text-muted",
                      )}
                      data-testid={`${config.testId}-anchor-count-${idKey(id)}`}
                      title={
                        count !== null
                          ? t("admin.assignmentEditor.assignedCount", { count })
                          : undefined
                      }
                    >
                      {count !== null ? count : "–"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>

        {/* Detail column */}
        <section
          className="flex min-h-[20rem] flex-col rounded-lg border border-hairline bg-surface"
          aria-label={t(counterpartSide.labelKey)}
        >
          {selectedId === null ? (
            <div className="flex flex-1 items-center justify-center p-6">
              <p className="w-full rounded-lg border border-dashed border-hairline px-4 py-10 text-center text-sm text-muted">
                {t("admin.assignmentEditor.selectAnchor", {
                  side: t(anchorSide.labelKey),
                })}
              </p>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between gap-2 border-b border-hairline px-3 py-2">
                <h2 className="min-w-0 truncate text-xs font-medium uppercase tracking-wide text-muted">
                  {t("admin.assignmentEditor.counterpartFor", {
                    counterpart: t(counterpartSide.labelKey),
                    anchor: selectedAnchorLabel,
                  })}
                </h2>
                <div className="flex shrink-0 items-center gap-2">
                  {busy && <Spinner className="size-4" />}
                  {savedFlash && (
                    <span
                      className="text-xs font-medium text-green"
                      data-testid={`${config.testId}-saved`}
                    >
                      {t("admin.assignmentEditor.saved")} ✓
                    </span>
                  )}
                  <button
                    type="button"
                    data-testid={`${config.testId}-bulk-all`}
                    disabled={pending || counterparts.length === 0}
                    onClick={runBulkAll}
                    className="rounded-md border border-hairline px-2 py-0.5 text-xs font-medium text-ink hover:border-accent/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-50"
                  >
                    {t("admin.assignmentEditor.all")}
                  </button>
                  <button
                    type="button"
                    data-testid={`${config.testId}-bulk-none`}
                    disabled={pending || assignedIdSet.size === 0}
                    onClick={runBulkNone}
                    className="rounded-md border border-hairline px-2 py-0.5 text-xs font-medium text-ink hover:border-accent/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-50"
                  >
                    {t("admin.assignmentEditor.none")}
                  </button>
                </div>
              </div>
              <div className="border-b border-hairline px-3 py-2">
                <label className="block">
                  <span className="sr-only">{t("admin.assignmentEditor.searchCounterpart")}</span>
                  <input
                    type="search"
                    value={counterpartFilter}
                    onChange={(e) => setCounterpartFilter(e.currentTarget.value)}
                    placeholder={t("admin.assignmentEditor.searchCounterpart")}
                    data-testid={`${config.testId}-search-counterpart`}
                    className="w-full rounded-md border border-hairline bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                  />
                </label>
              </div>
              <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-hairline">
                {counterpartsQ.isLoading || (assignedQ.isLoading && !assignedQ.data) ? (
                  <li className="flex justify-center px-3 py-8">
                    <Spinner className="size-5" />
                  </li>
                ) : counterparts.length === 0 ? (
                  <li className="px-3 py-8 text-center text-sm text-muted">
                    {t("admin.assignmentEditor.noCounterparts")}
                  </li>
                ) : filteredCounterparts.length === 0 ? (
                  <li className="px-3 py-8 text-center text-sm text-muted">
                    {t("admin.assignmentEditor.noMatches")}
                  </li>
                ) : (
                  filteredCounterparts.map((item) => {
                    const cId = counterpartSide.getId(item);
                    const checked = assignedIdSet.has(idKey(cId));
                    const sub = counterpartSide.getSubLabel?.(item);
                    return (
                      <li
                        key={idKey(cId)}
                        className="flex items-center px-3 py-2.5"
                        data-testid={`${config.testId}-counterpart-row-${idKey(cId)}`}
                      >
                        <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-3">
                          <input
                            type="checkbox"
                            className="size-4 accent-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                            checked={checked}
                            disabled={pending}
                            data-testid={`${config.testId}-counterpart-${idKey(cId)}`}
                            onChange={(e) =>
                              toggleM.mutate({
                                counterpartId: cId,
                                next: e.target.checked,
                              })
                            }
                          />
                          <span className="min-w-0">
                            <span className="block truncate text-sm text-ink">
                              {counterpartSide.getLabel(item)}
                            </span>
                            {sub ? (
                              <span className="block truncate text-xs text-muted">{sub}</span>
                            ) : null}
                          </span>
                        </label>
                      </li>
                    );
                  })
                )}
              </ul>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
