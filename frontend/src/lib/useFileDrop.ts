import { useCallback, useRef, useState, type DragEvent } from "react";

/** Image types the avatar upload takes. */
export const AVATAR_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export const AVATAR_IMAGE_ACCEPT = AVATAR_IMAGE_TYPES.join(",");

export function isAvatarImage(file: File): boolean {
  return (AVATAR_IMAGE_TYPES as readonly string[]).includes(file.type);
}

/** A vCard file. Contacts/Finder drags sometimes arrive without a MIME type,
 * so the extension decides first. */
export function isVcardFile(file: File): boolean {
  if (/\.vcf$/i.test(file.name)) return true;
  return ["text/vcard", "text/x-vcard", "text/directory"].includes(file.type.toLowerCase());
}

const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");

/**
 * Drop zone for files dragged in from the desktop (an image, a .vcf from
 * Apple Contacts). Spread `dropProps` on the zone and show an overlay while
 * `active`. A depth counter keeps the overlay from flickering when the
 * pointer crosses child elements (each fires its own enter/leave). Only
 * files `accept` lets through reach `onFiles`; a drop with none of them
 * calls `onReject` instead. An enabled zone stops the drag events, so a zone
 * nested in another (customer panel inside the customer list) takes the drop
 * alone and the outer overlay steps aside while the pointer is inside.
 */
export function useFileDrop({
  accept,
  onFiles,
  onReject,
  disabled = false,
}: {
  accept: (file: File) => boolean;
  onFiles: (files: File[]) => void;
  onReject?: (files: File[]) => void;
  disabled?: boolean;
}) {
  const [active, setActive] = useState(false);
  const depth = useRef(0);

  const onDragEnter = useCallback(
    (e: DragEvent) => {
      if (disabled || !hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      depth.current += 1;
      setActive(true);
    },
    [disabled],
  );

  const onDragOver = useCallback(
    (e: DragEvent) => {
      if (disabled || !hasFiles(e)) return;
      // Without preventDefault the browser refuses the drop (and opens the file).
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = "copy";
    },
    [disabled],
  );

  const onDragLeave = useCallback(
    (e: DragEvent) => {
      if (disabled || !hasFiles(e)) return;
      e.stopPropagation();
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setActive(false);
    },
    [disabled],
  );

  const onDrop = useCallback(
    (e: DragEvent) => {
      if (disabled || !hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      depth.current = 0;
      setActive(false);
      const files = Array.from(e.dataTransfer.files ?? []);
      const accepted = files.filter(accept);
      if (accepted.length > 0) onFiles(accepted);
      else if (files.length > 0) onReject?.(files);
    },
    [disabled, accept, onFiles, onReject],
  );

  return { active, dropProps: { onDragEnter, onDragOver, onDragLeave, onDrop } };
}
