import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChatBubbleIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { ChatInboxPanel } from "./ChatInboxPanel";
import { useChatRingState } from "./ChatRingContext";

/**
 * Header button for the web chat inbox. The numbers come from `useChatRing()`
 * (mounted once in the shell via `ChatRingProvider`, the only owner of those
 * queries); the button itself renders nothing for an agent without chat topics.
 * `variant` keeps the test ids of the mobile and desktop header copies apart.
 */
export function ChatInboxButton({ variant = "desktop" }: { variant?: "desktop" | "mobile" }) {
  const { t } = useTranslation();
  const { pending, topics, ready } = useChatRingState();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const prevPending = useRef<number | null>(null);
  const [announcement, setAnnouncement] = useState("");

  // Hand focus back to the button when the panel closes.
  useEffect(() => {
    if (wasOpen.current && !open) triggerRef.current?.focus();
    wasOpen.current = open;
  }, [open]);

  // Politely announce chats that start waiting after the inbox first loaded;
  // the ones already waiting at load time are not news.
  useEffect(() => {
    if (!ready) {
      prevPending.current = null;
      return;
    }
    const prev = prevPending.current;
    prevPending.current = pending;
    if (prev !== null && pending > prev) {
      setAnnouncement(t("webchat.agent.inbox.newWaiting", { count: pending }));
    } else if (pending === 0) {
      setAnnouncement("");
    }
  }, [pending, ready, t]);

  if (topics.length === 0) return null;

  const label = t("webchat.agent.inbox.buttonLabel", { count: pending });
  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        data-testid={`chat-inbox-trigger-${variant}`}
        aria-label={label}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={label}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "relative flex h-8 w-8 items-center justify-center rounded-lg text-ink/70 transition-colors duration-100 hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
          open && "bg-surface-subtle text-ink",
        )}
      >
        <ChatBubbleIcon className="text-[17px]" />
        {pending > 0 && (
          <span
            data-testid={`chat-inbox-badge-${variant}`}
            aria-hidden
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[9px] font-bold tabular-nums text-accent-ink"
          >
            {pending > 99 ? "99+" : pending}
          </span>
        )}
      </button>
      <span role="status" aria-live="polite" className="sr-only" data-testid={`chat-inbox-live-${variant}`}>
        {announcement}
      </span>
      {open && (
        <>
          <button
            type="button"
            aria-hidden
            tabIndex={-1}
            className="fixed inset-0 z-30 cursor-default"
            onClick={() => setOpen(false)}
          />
          <ChatInboxPanel onClose={() => setOpen(false)} />
        </>
      )}
    </div>
  );
}
