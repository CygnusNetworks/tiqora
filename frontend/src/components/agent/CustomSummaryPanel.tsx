import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { AiCustomSummaryOut } from "@/lib/ticketAiApi";
import { formatDateTime } from "@/lib/format";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { SparkIcon } from "@/components/ui/icons";
import { SummaryText } from "./SummaryText";

const INSTRUCTION_MAX_CHARS = 2000;

/**
 * "Eigene" tab of the summary card: the agent writes an instruction (or
 * picks a saved one) and gets a one-off summary back. The instruction text,
 * the running request and the last result all live in `AiPanel`, so closing
 * the hover card mid-run loses nothing.
 */
export function CustomSummaryPanel({
  instruction,
  onInstructionChange,
  onRun,
  pending,
  result,
  errorText,
  savedPrompts,
  onSavePrompt,
  onRemovePrompt,
}: {
  instruction: string;
  onInstructionChange: (value: string) => void;
  onRun: () => void;
  pending: boolean;
  result: (AiCustomSummaryOut & { instruction: string }) | null;
  errorText: string | null;
  savedPrompts: string[];
  onSavePrompt: (prompt: string) => void;
  onRemovePrompt: (prompt: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [copied, setCopied] = useState(false);
  const trimmed = instruction.trim();
  const alreadySaved = savedPrompts.includes(trimmed);

  return (
    <div className="space-y-2" data-testid="ai-custom-summary">
      {savedPrompts.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label={t("ticket.ai.custom.savedLabel")}>
          {savedPrompts.map((prompt, i) => (
            <li
              key={prompt}
              className="inline-flex max-w-full items-center rounded-full border border-hairline text-xs text-muted"
            >
              <button
                type="button"
                title={prompt}
                className="max-w-[18rem] truncate rounded-l-full py-0.5 pl-2.5 pr-1 hover:text-purple"
                data-testid={`ai-custom-summary-saved-${i}`}
                onClick={() => onInstructionChange(prompt)}
              >
                ★ {prompt}
              </button>
              <button
                type="button"
                className="rounded-r-full py-0.5 pl-1 pr-2 hover:text-danger"
                aria-label={t("ticket.ai.custom.removeSaved")}
                data-testid={`ai-custom-summary-remove-${i}`}
                onClick={() => onRemovePrompt(prompt)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <textarea
        id="ai-custom-summary-instruction"
        value={instruction}
        maxLength={INSTRUCTION_MAX_CHARS}
        rows={3}
        onChange={(e) => onInstructionChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && trimmed && !pending) {
            e.preventDefault();
            onRun();
          }
        }}
        aria-label={t("ticket.ai.custom.instructionLabel")}
        placeholder={t("ticket.ai.custom.placeholder")}
        className="w-full resize-y rounded-md border border-hairline bg-surface-subtle px-2.5 py-2 text-sm text-ink placeholder:text-muted focus:border-purple focus:outline-none"
        data-testid="ai-custom-summary-input"
      />

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="primary"
          className="bg-purple hover:bg-purple/90"
          disabled={!trimmed || pending}
          onClick={onRun}
          data-testid="ai-custom-summary-run"
        >
          {pending ? (
            <Spinner className="h-3.5 w-3.5" />
          ) : (
            <>
              <SparkIcon className="h-3.5 w-3.5" />
              {t("ticket.ai.custom.run")}
            </>
          )}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!trimmed || alreadySaved}
          onClick={() => onSavePrompt(trimmed)}
          data-testid="ai-custom-summary-save"
        >
          {alreadySaved ? t("ticket.ai.custom.saved") : t("ticket.ai.custom.save")}
        </Button>
        <span className="ml-auto text-[11px] text-muted">{t("ticket.ai.custom.hint")}</span>
      </div>

      {pending && (
        <p className="text-xs text-muted" data-testid="ai-custom-summary-running">
          {t("ticket.ai.custom.running")}
        </p>
      )}
      {errorText && (
        <p className="text-xs text-danger" data-testid="ai-custom-summary-error">
          {errorText}
        </p>
      )}

      {result && (
        <div
          className="space-y-2 rounded-lg border border-purple/40 bg-purple/5 p-3"
          data-testid="ai-custom-summary-result"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-purple">
              {t("ticket.ai.custom.resultLabel")}
            </span>
            <span className="text-[11px] text-muted">
              {formatDateTime(result.created_at, i18n.language)}
            </span>
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto"
              data-testid="ai-custom-summary-copy"
              onClick={() => {
                void navigator.clipboard
                  ?.writeText(result.summary_body)
                  .then(() => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  })
                  .catch(() => undefined);
              }}
            >
              {copied ? t("ticket.ai.custom.copied") : t("ticket.ai.custom.copy")}
            </Button>
          </div>
          <p className="line-clamp-2 text-xs italic text-muted" title={result.instruction}>
            {result.instruction}
          </p>
          <SummaryText body={result.summary_body} testId="ai-custom-summary-body" />
        </div>
      )}
    </div>
  );
}
