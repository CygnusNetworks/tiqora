import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/cn";

export type LightboxImage = {
  id: number;
  filename?: string | null;
  content_type?: string | null;
  content_size?: string | null;
};

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Full-bleed viewer for the image attachments of one article. Deliberately not
 * built on <Dialog>: this one is edge-to-edge over a dark scrim rather than a
 * bordered panel, so it owns its own copy of the modal mechanics (scroll lock,
 * focus trap, focus restore, Escape) plus ←/→ stepping through the set.
 */
export function AttachmentLightbox({
  ticketId,
  articleId,
  images,
  index,
  onIndexChange,
  onClose,
}: {
  ticketId: number;
  articleId: number;
  images: LightboxImage[];
  index: number;
  onIndexChange: (next: number) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);
  // Read via refs inside the effect: it must run once per open, not on every
  // parent re-render that hands us a fresh closure (see Dialog for the same
  // reasoning — re-running would bounce focus mid-interaction).
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const stepRef = useRef((_delta: number) => {});
  stepRef.current = (delta: number) =>
    onIndexChange((index + delta + images.length) % images.length);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const raf = requestAnimationFrame(() => {
      panelRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    });

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        stepRef.current(1);
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        stepRef.current(-1);
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !panel.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !panel.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      opener?.focus?.();
    };
  }, []);

  // A fresh image gets a fresh error state — otherwise one broken attachment
  // would poison every later frame in the set.
  useEffect(() => setFailed(false), [index]);

  const current = images[index];
  if (!current) return null;

  const name = current.filename || `attachment-${current.id}`;
  const meta = [
    current.content_size ? formatBytes(current.content_size) : "",
    (current.content_type ?? "").split(";")[0].trim(),
  ]
    .filter(Boolean)
    .join(" · ");
  const single = images.length < 2;

  return (
    <div
      ref={panelRef}
      className="fixed inset-0 z-50 flex flex-col bg-black/85 text-white backdrop-blur-sm motion-safe:animate-[dialog-fade_120ms_ease-out]"
      role="dialog"
      aria-modal="true"
      aria-label={t("ticket.imagePreview")}
      data-testid="attachment-lightbox"
      onClick={(e) => {
        // Scrim click closes; clicks that land on the image or the chrome do not.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex items-center gap-3 border-b border-white/10 px-4 py-2.5">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold" data-testid="lightbox-name">
            {name}
          </p>
          {meta && <p className="truncate font-mono text-[11px] text-white/60">{meta}</p>}
        </div>
        <span
          className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-white/60"
          data-testid="lightbox-count"
        >
          {t("ticket.imagePosition", { index: index + 1, count: images.length })}
        </span>
        <a
          className="inline-flex h-7 shrink-0 items-center rounded-md border border-white/20 bg-white/5 px-2.5 text-xs hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          href={api.attachmentDownloadUrl(ticketId, articleId, current.id, true)}
          download={current.filename ?? undefined}
        >
          {t("ticket.download")}
        </a>
        <button
          type="button"
          data-autofocus
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/20 bg-white/5 text-sm hover:bg-white/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          aria-label={t("common.close")}
          onClick={onClose}
        >
          ✕
        </button>
      </div>

      <div
        className="grid min-h-0 flex-1 grid-cols-[3rem_minmax(0,1fr)_3rem] items-center gap-2 p-4"
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <button
          type="button"
          className="justify-self-center rounded-full border border-white/20 bg-white/5 p-2 text-lg leading-none hover:bg-white/15 disabled:opacity-25 disabled:hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          aria-label={t("common.prev")}
          disabled={single}
          onClick={() => stepRef.current(-1)}
        >
          ‹
        </button>
        <div className="flex h-full min-h-0 items-center justify-center">
          {failed ? (
            <p className="text-sm text-white/70" data-testid="lightbox-failed">
              {t("ticket.previewFailed")}
            </p>
          ) : (
            <img
              key={current.id}
              src={api.attachmentDownloadUrl(ticketId, articleId, current.id, false)}
              alt={name}
              onError={() => setFailed(true)}
              className="max-h-full max-w-full rounded object-contain shadow-2xl motion-safe:animate-[dialog-pop_140ms_ease-out]"
              data-testid="lightbox-image"
            />
          )}
        </div>
        <button
          type="button"
          className="justify-self-center rounded-full border border-white/20 bg-white/5 p-2 text-lg leading-none hover:bg-white/15 disabled:opacity-25 disabled:hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          aria-label={t("common.next")}
          disabled={single}
          onClick={() => stepRef.current(1)}
        >
          ›
        </button>
      </div>

      {!single && (
        <div className="flex shrink-0 gap-2 overflow-x-auto border-t border-white/10 px-4 py-3 sm:justify-center">
          {images.map((img, i) => (
            <button
              key={img.id}
              type="button"
              aria-current={i === index}
              aria-label={img.filename || `attachment-${img.id}`}
              onClick={() => onIndexChange(i)}
              className={cn(
                "h-12 w-12 shrink-0 overflow-hidden rounded border transition-opacity focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                i === index
                  ? "border-accent opacity-100"
                  : "border-transparent opacity-50 hover:opacity-90",
              )}
            >
              <img
                src={api.attachmentDownloadUrl(ticketId, articleId, img.id, false)}
                alt=""
                loading="lazy"
                decoding="async"
                className="h-full w-full object-cover"
              />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
