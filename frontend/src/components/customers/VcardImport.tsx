import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import type { CustomerUserOut } from "@/lib/api";
import {
  apiErrorMessage,
  avatarApi,
  type VCardConflict,
  type VCardField,
  type VCardImportApplyOut,
  type VCardPreviewCard,
  type VCardSelection,
} from "@/lib/avatarApi";
import { cn } from "@/lib/cn";
import { initialsOf } from "./customerFormat";

const cardValue = (card: VCardPreviewCard, field: VCardField): string =>
  ((field === "email" ? card.emails[0] : card[field]) ?? "").trim();

/** The card meant for `login`: the one matched to it, else the only card. */
function cardFor(cards: VCardPreviewCard[], login: string): VCardPreviewCard | null {
  return cards.find((c) => c.match_login === login) ?? (cards.length === 1 ? cards[0] : null);
}

const fileKey = (file: File) => `${file.name}:${file.size}:${file.lastModified}`;

/** `login`: compare the cards with that customer (a card dropped on one customer). */
function usePreview(file: File, login?: string) {
  return useQuery({
    queryKey: ["vcard-import", "preview", fileKey(file), login ?? null],
    queryFn: () => avatarApi.previewVcards(file, login),
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
  });
}

function PhotoHint() {
  const { t } = useTranslation();
  return <p className="text-xs text-muted">{t("vcard.photoHint")}</p>;
}

/** "Vorname: Ilka, Telefon: …" — what a card would fill in. */
function FieldValues({ card, fields }: { card: VCardPreviewCard; fields: readonly string[] }) {
  const { t } = useTranslation();
  return (
    <ul className="space-y-0.5 text-sm">
      {fields.map((f) => (
        <li key={f} className="flex gap-2">
          <span className="w-24 shrink-0 text-muted">{t(`vcard.fields.${f}`)}</span>
          <span className="min-w-0 break-all text-ink">{cardValue(card, f as VCardField)}</span>
        </li>
      ))}
    </ul>
  );
}

function CardThumb({ card, size }: { card: VCardPreviewCard; size: number }) {
  return (
    <Avatar
      avatarUrl={card.photo_preview}
      initials={initialsOf(card.display_name || card.emails[0] || "?")}
      size={size}
    />
  );
}

const sideClass = (on: boolean) =>
  cn(
    "flex min-w-0 flex-col items-start gap-0.5 rounded-md border px-2.5 py-1.5 text-left transition-colors duration-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
    on ? "border-accent bg-accent-dim" : "border-hairline bg-surface hover:bg-surface-subtle",
  );

/**
 * Field by field: keep what Tiqora has or take the card's value. Nothing is
 * replaced unless picked here.
 */
function ConflictPicker({
  conflicts,
  replace,
  onChange,
}: {
  conflicts: VCardConflict[];
  replace: ReadonlySet<VCardField>;
  onChange: (next: Set<VCardField>) => void;
}) {
  const { t } = useTranslation();
  const set = (field: VCardField, take: boolean) => {
    const next = new Set(replace);
    if (take) next.add(field);
    else next.delete(field);
    onChange(next);
  };
  const allTaken = conflicts.every((c) => replace.has(c.field));
  return (
    <div className="space-y-2" data-testid="vcard-conflicts">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium text-ink">
          {t("vcard.conflictsTitle", { count: conflicts.length })}
        </p>
        {conflicts.length > 1 && (
          <button
            type="button"
            className="text-xs font-medium text-accent hover:underline"
            onClick={() => onChange(allTaken ? new Set() : new Set(conflicts.map((c) => c.field)))}
            data-testid="vcard-conflicts-all"
          >
            {allTaken ? t("vcard.keepAll") : t("vcard.takeAll")}
          </button>
        )}
      </div>
      <ul className="space-y-2.5">
        {conflicts.map((c) => {
          const take = replace.has(c.field);
          return (
            <li key={c.field} className="space-y-1" data-testid={`vcard-conflict-${c.field}`}>
              <p className="text-xs text-muted">{t(`vcard.fields.${c.field}`)}</p>
              <div className="grid grid-cols-2 gap-1.5" role="radiogroup" aria-label={t(`vcard.fields.${c.field}`)}>
                <button
                  type="button"
                  role="radio"
                  aria-checked={!take}
                  className={sideClass(!take)}
                  onClick={() => set(c.field, false)}
                  data-testid={`vcard-conflict-${c.field}-keep`}
                >
                  <span className="text-[11px] text-muted">{t("vcard.keepCurrent")}</span>
                  <span className="max-w-full break-all text-sm text-ink">{c.current}</span>
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={take}
                  className={sideClass(take)}
                  onClick={() => set(c.field, true)}
                  data-testid={`vcard-conflict-${c.field}-take`}
                >
                  <span className="text-[11px] text-muted">{t("vcard.takeIncoming")}</span>
                  <span className="max-w-full break-all text-sm text-ink">{c.incoming}</span>
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * A .vcf dropped on one customer: take its photo, fill blank fields and
 * settle the fields where card and customer disagree, after a short
 * confirmation of what changes. The card matched to this customer wins; a
 * file with exactly one card applies to the open customer even without a
 * matching address.
 */
export function VcardApplyDialog({
  file,
  customer,
  onClose,
  onApplied,
}: {
  file: File;
  customer: CustomerUserOut;
  onClose: () => void;
  /** After a successful apply; `photo` = the picture changed. */
  onApplied: (photo: boolean) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const previewQ = usePreview(file, customer.login);
  const cards = previewQ.data?.cards ?? [];
  const card = cardFor(cards, customer.login);
  const fields = card?.fill_fields ?? [];
  const conflicts = card?.conflicts ?? [];
  const [photo, setPhoto] = useState(true);
  const [fill, setFill] = useState(true);
  const [replace, setReplace] = useState<Set<VCardField>>(new Set());

  const applyM = useMutation({
    mutationFn: (selection: VCardSelection) => avatarApi.applyVcards(file, [selection]),
    onSuccess: async (_out, selection) => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["customers", customer.login] }),
        qc.invalidateQueries({ queryKey: ["customer-directory"] }),
      ]);
      onApplied(selection.photo);
    },
  });

  const takePhoto = Boolean(card?.has_photo) && photo;
  const takeFields = fields.length > 0 && fill;
  const nothing = !card || (!card.has_photo && fields.length === 0 && conflicts.length === 0);

  let body;
  if (previewQ.isLoading) {
    body = (
      <div className="flex justify-center py-6">
        <Spinner />
      </div>
    );
  } else if (previewQ.isError) {
    body = (
      <p className="text-sm text-danger" role="alert">
        {apiErrorMessage(previewQ.error, t("vcard.previewError"))}
      </p>
    );
  } else if (!card) {
    body = (
      <p className="text-sm text-muted" data-testid="vcard-apply-nomatch">
        {cards.length === 0 ? t("vcard.empty") : t("vcard.multipleCards")}
      </p>
    );
  } else {
    body = (
      <div className="space-y-5" data-testid="vcard-apply">
        <div className="flex items-center gap-3">
          <CardThumb card={card} size={44} />
          <div className="min-w-0">
            <p className="truncate font-medium text-ink">{card.display_name}</p>
            <p className="truncate text-xs text-muted">
              {[card.title, card.org, card.emails[0]].filter(Boolean).join(" · ")}
            </p>
          </div>
        </div>
        {card.match_login && card.match_login !== customer.login && (
          <p className="text-xs text-escalation">
            {t("vcard.otherCustomer", { name: card.match_name || card.match_login })}
          </p>
        )}
        {nothing ? (
          <p className="text-sm text-muted">{t("vcard.noChanges")}</p>
        ) : (
          <div className="space-y-4">
            {card.has_photo && (
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={photo}
                  onChange={(e) => setPhoto(e.target.checked)}
                  className="h-3.5 w-3.5 accent-accent"
                  data-testid="vcard-apply-photo"
                />
                {card.has_existing_avatar ? t("vcard.replacePhoto") : t("vcard.takePhoto")}
              </label>
            )}
            {fields.length > 0 && (
              <div className="space-y-1.5">
                <label className="flex items-center gap-2 text-sm text-ink">
                  <input
                    type="checkbox"
                    checked={fill}
                    onChange={(e) => setFill(e.target.checked)}
                    className="h-3.5 w-3.5 accent-accent"
                    data-testid="vcard-apply-fields"
                  />
                  {t("vcard.fillFields")}
                </label>
                <div className="pl-6">
                  <FieldValues card={card} fields={fields} />
                </div>
              </div>
            )}
            {conflicts.length > 0 && (
              <ConflictPicker conflicts={conflicts} replace={replace} onChange={setReplace} />
            )}
          </div>
        )}
        {!card.has_photo && <PhotoHint />}
      </div>
    );
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("vcard.applyTitle")}
      size="md"
      footer={
        <div className="flex items-center justify-end gap-2">
          {applyM.isError && (
            <p className="mr-auto text-xs text-danger" role="alert">
              {apiErrorMessage(applyM.error, t("vcard.applyError"))}
            </p>
          )}
          <Button variant="secondary" size="sm" onClick={onClose} disabled={applyM.isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            data-testid="vcard-apply-confirm"
            disabled={!card || (!takePhoto && !takeFields && replace.size === 0) || applyM.isPending}
            onClick={() =>
              card &&
              applyM.mutate({
                index: card.index,
                login: customer.login,
                photo: takePhoto,
                fields: takeFields,
                replace: [...replace],
              })
            }
          >
            {applyM.isPending ? <Spinner className="h-4 w-4" /> : t("vcard.apply")}
          </Button>
        </div>
      }
    >
      {body}
    </Dialog>
  );
}

type Choice = { photo: boolean; fields: boolean; replace: boolean };

/**
 * A .vcf with many contacts on the customer list: one row per card with what
 * it would change on the matched customer; checked rows are applied. Fields
 * where card and customer disagree are listed and replaced only when their
 * row's box is ticked. Cards without a match can be created through the
 * regular create form.
 */
export function VcardImportDialog({
  file,
  onClose,
  onCreate,
}: {
  file: File;
  onClose: () => void;
  onCreate: (prefill: {
    first_name: string;
    last_name: string;
    email: string;
    phone: string;
    mobile: string;
  }) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const previewQ = usePreview(file);
  const cards = useMemo(() => previewQ.data?.cards ?? [], [previewQ.data]);
  const [choices, setChoices] = useState<Record<number, Choice>>({});
  const [result, setResult] = useState<VCardImportApplyOut | null>(null);

  // Default: everything a matched card offers is checked — except replacing
  // what the customer already has, which is opt-in.
  const choiceOf = (card: VCardPreviewCard): Choice =>
    choices[card.index] ?? {
      photo: card.has_photo,
      fields: card.fill_fields.length > 0,
      replace: false,
    };
  const setChoice = (card: VCardPreviewCard, patch: Partial<Choice>) =>
    setChoices((prev) => ({ ...prev, [card.index]: { ...choiceOf(card), ...patch } }));

  const selections: VCardSelection[] = cards
    .filter((c) => c.match_login)
    .map((c) => {
      const ch = choiceOf(c);
      return {
        index: c.index,
        login: c.match_login as string,
        photo: c.has_photo && ch.photo,
        fields: c.fill_fields.length > 0 && ch.fields,
        replace: ch.replace ? c.conflicts.map((x) => x.field) : [],
      };
    })
    .filter((s) => s.photo || s.fields || (s.replace?.length ?? 0) > 0);

  const applyM = useMutation({
    mutationFn: () => avatarApi.applyVcards(file, selections),
    onSuccess: async (out) => {
      setResult(out);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["customers"] }),
        qc.invalidateQueries({ queryKey: ["customer-directory"] }),
      ]);
    },
  });

  const anyPhoto = cards.some((c) => c.has_photo);

  let body;
  if (previewQ.isLoading) {
    body = (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  } else if (previewQ.isError) {
    body = (
      <p className="text-sm text-danger" role="alert">
        {apiErrorMessage(previewQ.error, t("vcard.previewError"))}
      </p>
    );
  } else if (result) {
    body = (
      <p className="text-sm text-ink" data-testid="vcard-import-result">
        {t("vcard.applied", { count: result.applied.length })}
      </p>
    );
  } else if (cards.length === 0) {
    body = <p className="text-sm text-muted">{t("vcard.empty")}</p>;
  } else {
    body = (
      <div className="space-y-3">
        {previewQ.data?.truncated && (
          <p className="text-xs text-escalation">{t("vcard.truncated")}</p>
        )}
        {!anyPhoto && <PhotoHint />}
        <ul className="divide-y divide-hairline rounded-lg border border-hairline" data-testid="vcard-import-cards">
          {cards.map((card) => {
            const ch = choiceOf(card);
            return (
              <li
                key={card.index}
                className="flex items-start gap-3 px-3 py-2.5"
                data-testid={`vcard-card-${card.index}`}
              >
                <CardThumb card={card} size={36} />
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="truncate text-sm font-medium text-ink">{card.display_name}</p>
                  <p className="truncate text-xs text-muted">
                    {[card.emails.join(", "), card.org].filter(Boolean).join(" · ")}
                  </p>
                  {card.match_login ? (
                    <>
                      <p className="text-xs text-green">
                        {t("vcard.matched", { name: card.match_name || card.match_login })}
                      </p>
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink">
                        {card.has_photo && (
                          <label className="inline-flex items-center gap-1.5">
                            <input
                              type="checkbox"
                              checked={ch.photo}
                              onChange={(e) => setChoice(card, { photo: e.target.checked })}
                              className="h-3.5 w-3.5 accent-accent"
                              data-testid={`vcard-card-${card.index}-photo`}
                            />
                            {card.has_existing_avatar ? t("vcard.replacePhoto") : t("vcard.takePhoto")}
                          </label>
                        )}
                        {card.fill_fields.length > 0 && (
                          <label className="inline-flex items-center gap-1.5">
                            <input
                              type="checkbox"
                              checked={ch.fields}
                              onChange={(e) => setChoice(card, { fields: e.target.checked })}
                              className="h-3.5 w-3.5 accent-accent"
                              data-testid={`vcard-card-${card.index}-fields`}
                            />
                            {t("vcard.fillList", {
                              fields: card.fill_fields.map((f) => t(`vcard.fields.${f}`)).join(", "),
                            })}
                          </label>
                        )}
                        {!card.has_photo && card.fill_fields.length === 0 && card.conflicts.length === 0 && (
                          <span className="text-muted">{t("vcard.noChanges")}</span>
                        )}
                      </div>
                      {card.conflicts.length > 0 && (
                        <div className="space-y-1 rounded-md bg-surface-subtle px-2.5 py-2 text-xs">
                          <label className="inline-flex items-center gap-1.5 font-medium text-ink">
                            <input
                              type="checkbox"
                              checked={ch.replace}
                              onChange={(e) => setChoice(card, { replace: e.target.checked })}
                              className="h-3.5 w-3.5 accent-accent"
                              data-testid={`vcard-card-${card.index}-replace`}
                            />
                            {t("vcard.replaceList", { count: card.conflicts.length })}
                          </label>
                          <ul className="space-y-0.5 pl-5">
                            {card.conflicts.map((c) => (
                              <li key={c.field} className="flex flex-wrap gap-x-1.5 text-muted">
                                <span>{t(`vcard.fields.${c.field}`)}:</span>
                                <span className={cn("break-all", ch.replace && "line-through")}>{c.current}</span>
                                <span aria-hidden>→</span>
                                <span className="break-all text-ink">{c.incoming}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </>
                  ) : (
                    <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
                      {t("vcard.unmatched")}
                      <button
                        type="button"
                        className="font-medium text-accent hover:underline"
                        data-testid={`vcard-card-${card.index}-create`}
                        onClick={() =>
                          onCreate({
                            first_name: card.first_name ?? "",
                            last_name: card.last_name ?? "",
                            email: card.emails[0] ?? "",
                            phone: card.phone ?? "",
                            mobile: card.mobile ?? "",
                          })
                        }
                      >
                        {t("vcard.createCustomer")}
                      </button>
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("vcard.importTitle")}
      description={t("vcard.importDescription")}
      size="lg"
      footer={
        <div className="flex items-center justify-end gap-2">
          {applyM.isError && (
            <p className="mr-auto text-xs text-danger" role="alert">
              {apiErrorMessage(applyM.error, t("vcard.applyError"))}
            </p>
          )}
          {result ? (
            <Button variant="primary" size="sm" onClick={onClose} data-testid="vcard-import-done">
              {t("common.close")}
            </Button>
          ) : (
            <>
              <Button variant="secondary" size="sm" onClick={onClose} disabled={applyM.isPending}>
                {t("common.cancel")}
              </Button>
              <Button
                variant="primary"
                size="sm"
                data-testid="vcard-import-apply"
                disabled={selections.length === 0 || applyM.isPending}
                onClick={() => applyM.mutate()}
              >
                {applyM.isPending ? (
                  <Spinner className="h-4 w-4" />
                ) : (
                  t("vcard.applySelected", { count: selections.length })
                )}
              </Button>
            </>
          )}
        </div>
      }
    >
      {body}
    </Dialog>
  );
}
