import { useRef, useState, type ChangeEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  aiApi,
  type AiPromptPartCreate,
  type AiPromptPartOut,
  type AiPromptPartUpdate,
} from "@/lib/aiApi";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { useConfirm } from "@/components/ui/ConfirmDialog";

const MAX_PART_FILE_BYTES = 256 * 1024;

/** Same binary heuristic as the base-prompt upload: a null byte or a high
 * ratio of non-printable control chars means it isn't a text file. */
function looksBinary(text: string): boolean {
  let controlChars = 0;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code === 0 || (code < 32 && code !== 9 && code !== 10 && code !== 13)) controlChars++;
  }
  return controlChars / Math.max(text.length, 1) > 0.01;
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => reject(reader.error ?? new Error("file read failed"));
    reader.readAsText(file);
  });
}

/**
 * "Prompt-Bausteine" editor for one AI queue policy: ordered file/note parts
 * that are appended to the base system prompt at runtime (enabled parts, in
 * position order). Lives on the policy editor's basics tab, below the base
 * prompt — only in edit mode, since parts hang off an existing policy row.
 */
export function PromptPartsSection({ policyId }: { policyId: number }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [noteFormOpen, setNoteFormOpen] = useState(false);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteText, setNoteText] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editText, setEditText] = useState("");

  const partsKey = ["admin", "ai", "queues", policyId, "promptParts"];
  const partsQ = useQuery({
    queryKey: partsKey,
    queryFn: ({ signal }) => aiApi.listPromptParts(policyId, signal),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: partsKey });

  const createM = useMutation({
    mutationFn: (body: AiPromptPartCreate) => aiApi.createPromptPart(policyId, body),
    onSuccess: invalidate,
  });
  const updateM = useMutation({
    mutationFn: ({ partId, body }: { partId: number; body: AiPromptPartUpdate }) =>
      aiApi.updatePromptPart(policyId, partId, body),
    onSuccess: invalidate,
  });
  const deleteM = useMutation({
    mutationFn: (partId: number) => aiApi.deletePromptPart(policyId, partId),
    onSuccess: invalidate,
  });
  const reorderM = useMutation({
    mutationFn: (ids: number[]) => aiApi.reorderPromptParts(policyId, ids),
    onSuccess: invalidate,
  });

  const parts = partsQ.data ?? [];

  const handleFilesSelected = async (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    setFileError(null);
    for (const file of files) {
      if (file.size > MAX_PART_FILE_BYTES) {
        setFileError(t("admin.ai.promptParts.fileTooLarge", { name: file.name }));
        continue;
      }
      let text: string;
      try {
        text = await readFileAsText(file);
      } catch {
        setFileError(t("admin.ai.promptParts.fileBinary", { name: file.name }));
        continue;
      }
      if (looksBinary(text)) {
        setFileError(t("admin.ai.promptParts.fileBinary", { name: file.name }));
        continue;
      }
      await createM.mutateAsync({ kind: "file", title: file.name, content: text });
    }
  };

  const submitNote = async () => {
    const title = noteTitle.trim();
    const content = noteText.trim();
    if (!title || !content) return;
    await createM.mutateAsync({ kind: "note", title, content });
    setNoteTitle("");
    setNoteText("");
    setNoteFormOpen(false);
  };

  const move = (index: number, delta: -1 | 1) => {
    const ids = parts.map((p) => p.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    reorderM.mutate(ids);
  };

  const startEdit = (part: AiPromptPartOut) => {
    setEditingId(part.id);
    setEditText(part.content);
    setExpandedId(part.id);
  };

  const saveEdit = async (part: AiPromptPartOut) => {
    await updateM.mutateAsync({ partId: part.id, body: { content: editText } });
    setEditingId(null);
  };

  const busy = createM.isPending || updateM.isPending || deleteM.isPending || reorderM.isPending;

  return (
    <div className="block text-sm sm:col-span-2" data-testid="admin-ai-prompt-parts">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 font-medium text-ink">
          {t("admin.ai.promptParts.title")}
          <HelpPopover
            title={t("admin.ai.promptParts.title")}
            testId="admin-ai-prompt-parts-help"
          >
            {t("admin.help.ai.promptParts")}
          </HelpPopover>
        </span>
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            data-testid="admin-ai-prompt-parts-add-note"
            onClick={() => setNoteFormOpen((v) => !v)}
          >
            {t("admin.ai.promptParts.addNote")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            data-testid="admin-ai-prompt-parts-add-files"
            onClick={() => fileInputRef.current?.click()}
          >
            {t("admin.ai.promptParts.addFiles")}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".txt,.md,.markdown,text/plain,text/markdown"
            data-testid="admin-ai-prompt-parts-file-input"
            className="hidden"
            onChange={(e) => void handleFilesSelected(e)}
          />
        </div>
      </div>

      {noteFormOpen && (
        <div
          className="mb-2 space-y-2 rounded-md border border-hairline bg-surface-subtle p-3"
          data-testid="admin-ai-prompt-parts-note-form"
        >
          <input
            value={noteTitle}
            onChange={(e) => setNoteTitle(e.target.value)}
            placeholder={t("admin.ai.promptParts.noteTitlePlaceholder")}
            data-testid="admin-ai-prompt-parts-note-title"
            className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none"
          />
          <textarea
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            placeholder={t("admin.ai.promptParts.noteTextPlaceholder")}
            rows={3}
            data-testid="admin-ai-prompt-parts-note-text"
            className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 font-mono text-xs text-ink placeholder:text-muted focus:border-accent focus:outline-none"
          />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setNoteFormOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              variant="primary"
              size="sm"
              data-testid="admin-ai-prompt-parts-note-save"
              disabled={!noteTitle.trim() || !noteText.trim() || createM.isPending}
              onClick={() => void submitNote()}
            >
              {t("admin.ai.promptParts.noteSave")}
            </Button>
          </div>
        </div>
      )}

      {parts.length === 0 && !partsQ.isLoading ? (
        <p className="text-xs text-muted" data-testid="admin-ai-prompt-parts-empty">
          {t("admin.ai.promptParts.empty")}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {parts.map((part, index) => {
            const expanded = expandedId === part.id;
            const editing = editingId === part.id;
            return (
              <li
                key={part.id}
                className={cn(
                  "rounded-md border border-hairline bg-surface-subtle px-3 py-2",
                  !part.enabled && "opacity-60",
                )}
                data-testid={`admin-ai-prompt-part-${part.id}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span aria-hidden className="text-sm">
                    {part.kind === "file" ? "📄" : "📝"}
                  </span>
                  <button
                    type="button"
                    className="min-w-0 flex-1 truncate text-left text-[13px] font-medium text-ink hover:underline"
                    data-testid={`admin-ai-prompt-part-toggle-${part.id}`}
                    onClick={() => {
                      setExpandedId(expanded ? null : part.id);
                      if (editing) setEditingId(null);
                    }}
                  >
                    {part.title}
                  </button>
                  <span className="font-mono text-[11px] tabular-nums text-muted">
                    {t("admin.ai.promptParts.charCount", {
                      formatted: new Intl.NumberFormat(i18n.language).format(part.content.length),
                    })}
                  </span>
                  <label className="inline-flex items-center gap-1 text-[11px] text-muted">
                    <input
                      type="checkbox"
                      checked={part.enabled}
                      disabled={busy}
                      data-testid={`admin-ai-prompt-part-enabled-${part.id}`}
                      onChange={(e) =>
                        updateM.mutate({ partId: part.id, body: { enabled: e.target.checked } })
                      }
                    />
                    {t("admin.ai.promptParts.enabled")}
                  </label>
                  <div className="flex items-center">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label={t("admin.ai.promptParts.moveUp")}
                      data-testid={`admin-ai-prompt-part-up-${part.id}`}
                      disabled={index === 0 || busy}
                      onClick={() => move(index, -1)}
                    >
                      ▲
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label={t("admin.ai.promptParts.moveDown")}
                      data-testid={`admin-ai-prompt-part-down-${part.id}`}
                      disabled={index === parts.length - 1 || busy}
                      onClick={() => move(index, 1)}
                    >
                      ▼
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      data-testid={`admin-ai-prompt-part-delete-${part.id}`}
                      disabled={busy}
                      onClick={async () => {
                        const ok = await confirm({
                          title: t("admin.ai.promptParts.deleteTitle"),
                          message: t("admin.ai.promptParts.deleteConfirm", { title: part.title }),
                          variant: "danger",
                        });
                        if (ok) deleteM.mutate(part.id);
                      }}
                    >
                      ✕
                    </Button>
                  </div>
                </div>
                {expanded && (
                  <div className="mt-2 space-y-2">
                    {editing ? (
                      <>
                        <textarea
                          value={editText}
                          onChange={(e) => setEditText(e.target.value)}
                          rows={6}
                          data-testid={`admin-ai-prompt-part-edit-${part.id}`}
                          className="w-full rounded-md border border-hairline bg-surface px-3 py-1.5 font-mono text-xs text-ink focus:border-accent focus:outline-none"
                        />
                        <div className="flex justify-end gap-2">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => setEditingId(null)}
                          >
                            {t("common.cancel")}
                          </Button>
                          <Button
                            type="button"
                            variant="primary"
                            size="sm"
                            data-testid={`admin-ai-prompt-part-edit-save-${part.id}`}
                            disabled={!editText.trim() || updateM.isPending}
                            onClick={() => void saveEdit(part)}
                          >
                            {t("admin.ai.promptParts.save")}
                          </Button>
                        </div>
                      </>
                    ) : (
                      <>
                        <pre
                          className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-surface p-2 font-mono text-[11px] leading-snug text-ink/80"
                          data-testid={`admin-ai-prompt-part-content-${part.id}`}
                        >
                          {part.content}
                        </pre>
                        <div className="flex justify-end">
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            data-testid={`admin-ai-prompt-part-edit-button-${part.id}`}
                            onClick={() => startEdit(part)}
                          >
                            {t("admin.ai.promptParts.edit")}
                          </Button>
                        </div>
                      </>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {fileError && (
        <p className="mt-1 text-xs text-escalation" data-testid="admin-ai-prompt-parts-file-error">
          {fileError}
        </p>
      )}
      {(createM.isError || updateM.isError || deleteM.isError || reorderM.isError) && (
        <p className="mt-1 text-xs text-escalation" data-testid="admin-ai-prompt-parts-error">
          {t("admin.ai.promptParts.genericError")}
        </p>
      )}
      {confirmDialog}
    </div>
  );
}
