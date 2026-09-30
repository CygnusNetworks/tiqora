import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { elapsedToMinutes } from "@/lib/phoneCall";
import { ComposerTimeChip } from "../ComposerTimeChip";
import { AttachmentChips } from "../telegram/ComposerChips";
import type { ChatAttachment } from "../telegram/useChatAttachments";

/**
 * Footer bar under the Gesprächsnotiz (Decision 8): "Notiz aufbereiten"
 * (passed in as `refine`), "Datei" with the attached files, and on the right
 * the time accounting chip, which follows the call timer until the agent
 * types a value.
 */
export function PhoneNoteFooter({
  refine,
  elapsed,
  timeUnits,
  onTimeUnitsChange,
  attachments,
  onAttach,
  onRemoveAttachment,
}: {
  refine: ReactNode;
  elapsed: number;
  /** Minutes typed by the agent; `null` = follow the timer. */
  timeUnits: string | null;
  onTimeUnitsChange: (v: string) => void;
  attachments: ChatAttachment[];
  onAttach: (files: FileList) => void;
  onRemoveAttachment: (id: number) => void;
}) {
  const { t } = useTranslation();
  // The time chip keeps its own text: re-key it for each new timer minute,
  // but only while the agent has not typed a value.
  const autoMinutes = elapsedToMinutes(elapsed);
  const [chipKey, setChipKey] = useState(0);
  useEffect(() => {
    if (timeUnits === null) setChipKey(autoMinutes);
  }, [autoMinutes, timeUnits]);

  return (
    <div
      className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 border-t border-hairline bg-surface-subtle px-2 py-1.5 text-xs"
      data-testid="new-ticket-note-footer"
    >
      {refine}
      <label className="cursor-pointer rounded-md border border-hairline bg-surface px-2 py-[3px] text-muted hover:text-ink focus-within:outline focus-within:outline-2 focus-within:outline-accent">
        <span aria-hidden>📎</span> {t("newTicket.attachFile")}
        <input
          type="file"
          multiple
          className="sr-only"
          data-testid="new-ticket-attach-input"
          onChange={(e) => {
            if (e.target.files) onAttach(e.target.files);
            e.target.value = "";
          }}
        />
      </label>
      <AttachmentChips items={attachments} onRemove={onRemoveAttachment} />
      <span className="ml-auto">
        <ComposerTimeChip
          key={chipKey}
          value={timeUnits ?? (autoMinutes > 0 ? String(autoMinutes) : "")}
          onChange={onTimeUnitsChange}
          testId="new-ticket-time"
        />
      </span>
    </div>
  );
}
