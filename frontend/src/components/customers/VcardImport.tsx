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
  type VCardImportApplyOut,
  type VCardPreviewCard,
  type VCardSelection,
} from "@/lib/avatarApi";
import { initialsOf } from "./customerFormat";

/** Fields a vCard can fill — only blank ones, as `PATCH /customers/{login}`. */
const FILL_FIELDS = ["first_name", "last_name", "email", "phone", "mobile"] as const;
type FillField = (typeof FILL_FIELDS)[number];

const cardValue = (card: VCardPreviewCard, field: FillField): string =>
  ((field === "email" ? card.emails[0] : card[field]) ?? "").trim();

/** Blank fields of `customer` the card has a value for. */
function customerFillFields(
  customer: Pick<CustomerUserOut, FillField>,
  card: VCardPreviewCard,
): FillField[] {
  return FILL_FIELDS.filter((f) => !(customer[f] ?? "").trim() && cardValue(card, f) !== "");
}

/** The card meant for `login`: the one matched to it, else the only card. */
function cardFor(cards: VCardPreviewCard[], login: string): VCardPreviewCard | null {
  return cards.find((c) => c.match_login === login) ?? (cards.length === 1 ? cards[0] : null);
}

const fileKey = (file: File) => `${file.name}:${file.size}:${file.lastModified}`;

function usePreview(file: File) {
  return useQuery({
    queryKey: ["vcard-import", "preview", fileKey(file)],
    queryFn: () => avatarApi.previewVcards(file),
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
          <span className="min-w-0 break-all text-ink">{cardValue(card, f as FillField)}</span>
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

/**
 * A .vcf dropped on one customer: take its photo and fill blank fields after
 * a short confirmation of what changes. The card matched to this customer
 * wins; a file with exactly one card applies to the open customer even
 * without a matching address.
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
  const previewQ = usePreview(file);
  const cards = previewQ.data?.cards ?? [];
  const card = cardFor(cards, customer.login);
  const fields = card ? customerFillFields(customer, card) : [];
  const [photo, setPhoto] = useState(true);
  const [fill, setFill] = useState(true);

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
  const nothing = !card || (!card.has_photo && fields.length === 0);

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
      <div className="space-y-4" data-testid="vcard-apply">
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
          <div className="space-y-3">
            {card.has_photo && (
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={photo}
                  onChange={(e) => setPhoto(e.target.checked)}
                  data-testid="vcard-apply-photo"
                />
                {t("vcard.takePhoto")}
              </label>
            )}
            {fields.length > 0 && (
              <div className="space-y-1.5">
                <label className="flex items-center gap-2 text-sm text-ink">
                  <input
                    type="checkbox"
                    checked={fill}
                    onChange={(e) => setFill(e.target.checked)}
                    data-testid="vcard-apply-fields"
                  />
                  {t("vcard.fillFields")}
                </label>
                <div className="pl-6">
                  <FieldValues card={card} fields={fields} />
                </div>
              </div>
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
      size="sm"
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
            disabled={!card || (!takePhoto && !takeFields) || applyM.isPending}
            onClick={() =>
              card &&
              applyM.mutate({
                index: card.index,
                login: customer.login,
                photo: takePhoto,
                fields: takeFields,
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

type Choice = { photo: boolean; fields: boolean };

/**
 * A .vcf with many contacts on the customer list: one row per card with what
 * it would change on the matched customer; checked rows are applied. Cards
 * without a match can be created through the regular create form.
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

  // Default: everything a matched card offers is checked.
  const choiceOf = (card: VCardPreviewCard): Choice =>
    choices[card.index] ?? { photo: card.has_photo, fields: card.fill_fields.length > 0 };
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
      };
    })
    .filter((s) => s.photo || s.fields);

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
                              data-testid={`vcard-card-${card.index}-fields`}
                            />
                            {t("vcard.fillList", {
                              fields: card.fill_fields.map((f) => t(`vcard.fields.${f}`)).join(", "),
                            })}
                          </label>
                        )}
                        {!card.has_photo && card.fill_fields.length === 0 && (
                          <span className="text-muted">{t("vcard.noChanges")}</span>
                        )}
                      </div>
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
