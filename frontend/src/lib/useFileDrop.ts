import { useCallback, useRef, useState, type DragEvent } from "react";

/** Image types the avatar upload takes. */
export const AVATAR_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export const AVATAR_IMAGE_ACCEPT = AVATAR_IMAGE_TYPES.join(",");

export function isAvatarImage(file: File): boolean {
  return (AVATAR_IMAGE_TYPES as readonly string[]).includes(file.type);
}

/** MIME types a contact drag may carry as data instead of a file. */
const VCARD_TYPES = ["text/vcard", "text/x-vcard", "text/directory"] as const;

/** A vCard file. Contacts/Finder drags sometimes arrive without a MIME type,
 * so the extension decides first. */
export function isVcardFile(file: File): boolean {
  if (/\.vcf$/i.test(file.name)) return true;
  return (VCARD_TYPES as readonly string[]).includes(file.type.toLowerCase());
}

/** What the drop handed over, for a reject message that says what arrived. */
export type DropRejection = { files: File[]; types: string[] };

const dragTypes = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []);

// A drag that started on this page (selected text, a link, a vCard dragged
// out) must not light up the drop zones.
let internalDrag = false;
if (typeof document !== "undefined") {
  document.addEventListener("dragstart", () => (internalDrag = true), true);
  document.addEventListener("dragend", () => (internalDrag = false), true);
  document.addEventListener("drop", () => (internalDrag = false), true);
}

/**
 * Files of a drop, plus a contact that arrived as text. Apple Contacts hands
 * a dragged card over as a promised file, which not every browser turns into
 * a `File`; most also put the card on the pasteboard as `text/vcard` or plain
 * text starting with `BEGIN:VCARD`. That text becomes a `kontakt.vcf` file.
 */
export function droppedFiles(dt: DataTransfer, vcardText: boolean): File[] {
  const files = Array.from(dt.files ?? []);
  if (files.length > 0 || !vcardText) return files;
  for (const type of [...VCARD_TYPES, "text/plain"]) {
    let text = "";
    try {
      text = dt.getData(type);
    } catch {
      continue;
    }
    if (/^\s*BEGIN:VCARD/i.test(text)) {
      return [new File([text], "kontakt.vcf", { type: "text/vcard" })];
    }
  }
  return [];
}

/**
 * Drop zone for files dragged in from the desktop (an image, a .vcf from
 * Apple Contacts). Spread `dropProps` on the zone and show an overlay while
 * `active`. A depth counter keeps the overlay from flickering when the
 * pointer crosses child elements (each fires its own enter/leave). Only
 * files `accept` lets through reach `onFiles`; a drop with none of them
 * calls `onReject` instead. An enabled zone stops the drag events, so a zone
 * nested in another (customer panel inside the customer list) takes the drop
 * alone and the outer overlay steps aside while the pointer is inside.
 *
 * With `vcardText` the zone also opens for drags that carry text instead of
 * files — the browser only reveals the data on drop, so a contact can arrive
 * that way (see `droppedFiles`).
 */
export function useFileDrop({
  accept,
  onFiles,
  onReject,
  disabled = false,
  vcardText = false,
}: {
  accept: (file: File) => boolean;
  onFiles: (files: File[]) => void;
  onReject?: (rejection: DropRejection) => void;
  disabled?: boolean;
  vcardText?: boolean;
}) {
  const [active, setActive] = useState(false);
  const depth = useRef(0);

  const wanted = useCallback(
    (e: DragEvent) => {
      if (disabled || internalDrag) return false;
      const types = dragTypes(e);
      if (types.includes("Files")) return true;
      return vcardText && types.some((t) => t === "text/plain" || (VCARD_TYPES as readonly string[]).includes(t));
    },
    [disabled, vcardText],
  );

  const onDragEnter = useCallback(
    (e: DragEvent) => {
      if (!wanted(e)) return;
      e.preventDefault();
      e.stopPropagation();
      depth.current += 1;
      setActive(true);
    },
    [wanted],
  );

  const onDragOver = useCallback(
    (e: DragEvent) => {
      if (!wanted(e)) return;
      // Without preventDefault the browser refuses the drop (and opens the file).
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = "copy";
    },
    [wanted],
  );

  const onDragLeave = useCallback(
    (e: DragEvent) => {
      if (!wanted(e)) return;
      e.stopPropagation();
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setActive(false);
    },
    [wanted],
  );

  const onDrop = useCallback(
    (e: DragEvent) => {
      if (!wanted(e)) return;
      e.preventDefault();
      e.stopPropagation();
      depth.current = 0;
      setActive(false);
      const files = droppedFiles(e.dataTransfer, vcardText);
      const accepted = files.filter(accept);
      if (accepted.length > 0) onFiles(accepted);
      else onReject?.({ files, types: dragTypes(e) });
    },
    [wanted, vcardText, accept, onFiles, onReject],
  );

  return { active, dropProps: { onDragEnter, onDragOver, onDragLeave, onDrop } };
}
