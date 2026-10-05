import { useTranslation } from "react-i18next";
import { dialHref } from "@/lib/phoneCall";
import { useOriginate } from "./useOriginate";

/**
 * A phone number as a click-to-call link. With click-to-dial configured
 * (`originate`), a click rings the agent's desk phone and then dials;
 * Ctrl/Cmd-click still follows the `tel:`/`sip:` link. `onDial` runs
 * right away for the plain link, and only after a successful dial otherwise
 * (e.g. to open the call form).
 */
export function DialLink({
  number,
  onDial,
  testId,
  ticketId,
  name,
}: {
  number: string;
  onDial?: () => void;
  testId?: string;
  ticketId?: number;
  name?: string;
}) {
  const { t } = useTranslation();
  const { enabled, scheme, status, dial } = useOriginate();
  return (
    <span className="inline-flex items-center gap-2">
      <a
        href={dialHref(number, scheme)}
        onClick={(e) => {
          if (enabled && !e.metaKey && !e.ctrlKey) {
            e.preventDefault();
            // The caller's follow-up (e.g. opening a form) waits for the PBX to
            // accept the call; on failure we stay here and show the error.
            void dial(number, { ticketId, name }).then((ok) => {
              if (ok) onDial?.();
            });
            return;
          }
          onDial?.();
        }}
        data-testid={testId}
        className="font-mono text-accent hover:underline"
      >
        {number}
      </a>
      {status.kind === "ringing" && (
        <span
          className="text-xs text-muted"
          data-testid={testId ? `${testId}-status` : undefined}
        >
          {t("phone.dialRinging", { extension: status.extension })}
        </span>
      )}
      {status.kind === "error" && (
        <span className="text-xs text-danger" role="alert">
          {status.message}
        </span>
      )}
    </span>
  );
}
