import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";

const FIELD_CLASS =
  "w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-[13.5px] text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent";

const toolbarBtnCls =
  "inline-flex h-6 min-w-6 items-center justify-center rounded px-1 text-xs text-muted hover:bg-surface hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

type ToolbarCommand = {
  command: string;
  value?: string;
  label: string;
  glyph: string;
  testid: string;
};

// Minimal, no-dependency formatting bar. document.execCommand is deprecated
// but still the smallest way to get Bold/Italic/lists on a contentEditable
// div without pulling in a full editor library — acceptable for the small
// surface this composer needs.
const TOOLBAR: ToolbarCommand[] = [
  { command: "bold", label: "Bold", glyph: "B", testid: "bold" },
  { command: "italic", label: "Italic", glyph: "I", testid: "italic" },
  { command: "underline", label: "Underline", glyph: "U", testid: "underline" },
  { command: "insertUnorderedList", label: "Bullet list", glyph: "•", testid: "ul" },
  { command: "insertOrderedList", label: "Numbered list", glyph: "1.", testid: "ol" },
  { command: "removeFormat", label: "Remove format", glyph: "Tx", testid: "clear" },
];

/**
 * New-ticket / phone-note body editor. Plain textarea when the queue's
 * ``Frontend::RichText`` sysconfig flag is off; a minimal contentEditable
 * WYSIWYG (Bold/Italic/Underline/lists/remove-format via
 * ``document.execCommand``) when it's on. Emits plain text or HTML
 * respectively — the caller picks the matching ``content_type``.
 */
export function ComposerBody({
  richText,
  value,
  onChange,
  testId,
}: {
  richText: boolean;
  value: string;
  onChange: (v: string) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  const editorRef = useRef<HTMLDivElement>(null);

  // contentEditable is inherently uncontrolled; only push `value` into the DOM
  // when it changed from outside (e.g. a future template insert), not on every
  // keystroke — that would fight the caret position.
  useEffect(() => {
    if (!richText) return;
    const el = editorRef.current;
    if (el && el.innerHTML !== value) el.innerHTML = value;
  }, [richText, value]);

  const runCommand = (command: string, commandValue?: string) => {
    editorRef.current?.focus();
    // jsdom implements execCommand as a no-op stub — guard so tests don't
    // throw when a button is clicked without a working editing host.
    if (typeof document.execCommand === "function") {
      document.execCommand(command, false, commandValue);
    }
    if (editorRef.current) onChange(editorRef.current.innerHTML);
  };

  if (!richText) {
    return (
      <textarea
        data-testid={testId}
        rows={10}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`${FIELD_CLASS} font-mono resize-y`}
      />
    );
  }

  return (
    <div className="rounded-md border border-hairline bg-surface-subtle">
      <div
        className="flex flex-wrap items-center gap-0.5 border-b border-hairline p-1"
        data-testid={testId ? `${testId}-toolbar` : undefined}
      >
        {TOOLBAR.map((btn) => (
          <button
            key={btn.command}
            type="button"
            title={btn.label}
            aria-label={btn.label}
            className={toolbarBtnCls}
            data-testid={testId ? `${testId}-toolbar-${btn.testid}` : undefined}
            // Prevent the mousedown from stealing focus off the editor before
            // execCommand runs against the current selection.
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => runCommand(btn.command, btn.value)}
          >
            {btn.glyph}
          </button>
        ))}
      </div>
      <div
        ref={editorRef}
        contentEditable
        suppressContentEditableWarning
        data-testid={testId}
        aria-label={t("newTicket.message")}
        className={cn(
          "min-h-[12rem] px-3 py-2 text-[13.5px] text-ink focus-visible:outline-none",
          "[&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5",
        )}
        onInput={(e) => onChange(e.currentTarget.innerHTML)}
      />
    </div>
  );
}
