import { useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { PencilIcon, TrashIcon, UploadIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { AVATAR_IMAGE_ACCEPT } from "@/lib/useFileDrop";
import type { AvatarEditor } from "./useAvatarEditor";

/**
 * The avatar with a small edit button on its corner: upload a picture
 * (PNG/JPEG/WebP) or remove it. Without `canEdit` it is just the avatar.
 */
export function EditableAvatar({
  editor,
  canEdit,
  kind,
  children,
  testId,
}: {
  editor: AvatarEditor;
  canEdit: boolean;
  kind: "photo" | "logo";
  children: ReactNode;
  testId: string;
}) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement | null>(null);
  if (!canEdit) return <>{children}</>;
  const editLabel = kind === "logo" ? t("avatars.editLogo") : t("avatars.editPhoto");
  return (
    <span className="relative inline-flex shrink-0">
      {children}
      <input
        ref={inputRef}
        type="file"
        accept={AVATAR_IMAGE_ACCEPT}
        className="hidden"
        data-testid={`${testId}-file`}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void editor.uploadFile(file);
        }}
      />
      <Menu
        align="left"
        trigger={({ open, ref, toggleProps }) => (
          <button
            ref={ref}
            type="button"
            {...toggleProps}
            disabled={editor.busy}
            aria-label={editLabel}
            title={editLabel}
            data-testid={`${testId}-menu`}
            className={cn(
              "absolute -bottom-1 -right-1 grid h-6 w-6 place-items-center rounded-full border border-hairline bg-surface text-muted shadow-sm transition-colors duration-100 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-60",
              open && "text-ink",
            )}
          >
            <PencilIcon className="text-[12px]" />
          </button>
        )}
      >
        <div className="py-1">
          <MenuItem
            icon={<UploadIcon />}
            testId={`${testId}-upload`}
            onSelect={() => inputRef.current?.click()}
          >
            {kind === "logo" ? t("avatars.uploadLogo") : t("avatars.uploadPhoto")}
          </MenuItem>
          <MenuItem
            icon={<TrashIcon />}
            danger
            testId={`${testId}-remove`}
            onSelect={() => void editor.removeImage()}
          >
            {kind === "logo" ? t("avatars.removeLogo") : t("avatars.removePhoto")}
          </MenuItem>
        </div>
      </Menu>
    </span>
  );
}

/** Dashed overlay over a drop zone while files are dragged over it. Render
 * it as the zone's LAST child: inside a `space-y-*` parent a first child
 * would push the real content down while dragging; the inline margin beats
 * the `space-y` rule for the overlay itself. */
export function DropOverlay({ active, children }: { active: boolean; children: ReactNode }) {
  if (!active) return null;
  return (
    <div
      className="pointer-events-none absolute inset-0 z-20 grid place-items-center rounded-lg border-2 border-dashed border-accent bg-accent/10 p-4 text-center text-sm font-medium text-accent"
      style={{ marginTop: 0 }}
      data-testid="drop-overlay"
    >
      {children}
    </div>
  );
}
