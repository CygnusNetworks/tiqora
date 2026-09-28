import { useCallback, useRef, useState } from "react";
import type { ArticleAttachmentIn } from "@/lib/api";

/** Bot API upload ceiling for documents; the batch is refused as a whole
 * rather than half-attached. */
export const MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024;

export type ChatAttachment = {
  id: number;
  name: string;
  size: number;
  type: string;
  /** Base64 without the data-URL prefix; null while still encoding. */
  data: string | null;
};

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result ?? "");
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Files queued in the chat composer. Encoded right away so the send doesn't
 * stall on a large read; `encoding` keeps the send button disabled until
 * every chip has its payload.
 */
export function useChatAttachments() {
  const [items, setItems] = useState<ChatAttachment[]>([]);
  const [error, setError] = useState<"tooLarge" | "readFailed" | null>(null);
  const nextId = useRef(0);

  const add = useCallback(
    (files: FileList | File[]) => {
      const list = Array.from(files);
      if (list.length === 0) return;
      const total =
        items.reduce((sum, a) => sum + a.size, 0) + list.reduce((sum, f) => sum + f.size, 0);
      if (total > MAX_ATTACHMENT_BYTES) {
        setError("tooLarge");
        return;
      }
      setError(null);
      const added = list.map((f) => ({
        file: f,
        item: {
          id: nextId.current++,
          name: f.name,
          size: f.size,
          type: f.type || "application/octet-stream",
          data: null,
        } as ChatAttachment,
      }));
      setItems((prev) => [...prev, ...added.map((a) => a.item)]);
      for (const { file, item } of added) {
        readBase64(file).then(
          (data) => setItems((prev) => prev.map((a) => (a.id === item.id ? { ...a, data } : a))),
          () => {
            setItems((prev) => prev.filter((a) => a.id !== item.id));
            setError("readFailed");
          },
        );
      }
    },
    [items],
  );

  const remove = useCallback((id: number) => {
    setItems((prev) => prev.filter((a) => a.id !== id));
    setError(null);
  }, []);

  const clear = useCallback(() => {
    setItems([]);
    setError(null);
  }, []);

  const payload: ArticleAttachmentIn[] = items
    .filter((a) => a.data !== null)
    .map((a) => ({ filename: a.name, content_type: a.type, content_base64: a.data as string }));

  return {
    items,
    error,
    encoding: items.some((a) => a.data === null),
    add,
    remove,
    clear,
    payload,
  };
}
