import type { TFunction } from "i18next";
import type { DropRejection } from "@/lib/useFileDrop";

/**
 * Why a drop was refused. A drop with files got the wrong kind (`wrongKey`);
 * one without files carried no contact the browser would hand over — say
 * which types did arrive, so a Contacts drag that fails can be told apart from
 * a stray text drag.
 */
export function dropRejectMessage(t: TFunction, rejection: DropRejection, wrongKey: string): string {
  if (rejection.files.length > 0) return t(wrongKey);
  return t("vcard.noCardInDrag", { types: rejection.types.join(", ") || "–" });
}
