import { useCallback, useRef, useState } from "react";
import type { ArticleAttachmentIn } from "@/lib/api";

/** Total raw size the composer accepts. Telegram would take 50 MB per
 * document, but the files travel base64-encoded (×4/3) inside one JSON POST
 * and production nginx caps request bodies at 25 MB (`client_max_body_size
 * 25M`) — 18 MB raw is ~24 MB on the wire, leaving room for the rest of the
 * payload. A batch that would cross it is refused as a whole rather than
 * half-attached. */
export const MAX_ATTACHMENT_BYTES = 18 * 1024 * 1024;

/** The API takes at most 10 attachments per reply (`max_length=10`). */
export const MAX_ATTACHMENTS = 10;

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
  const [error, setError] = useState<"tooLarge" | "tooMany" | "readFailed" | null>(null);
  const nextId = useRef(0);
  // Source of truth for the size check: two adds in the same tick (a paste
  // and a drop, a multi-event drop) must both see each other, which the
  // render-time `items` would not.
  const itemsRef = useRef<ChatAttachment[]>([]);
  const commit = useCallback((next: ChatAttachment[]) => {
    itemsRef.current = next;
    setItems(next);
  }, []);

  const add = useCallback(
    (files: FileList | File[]) => {
      const list = Array.from(files);
      if (list.length === 0) return;
      if (itemsRef.current.length + list.length > MAX_ATTACHMENTS) {
        setError("tooMany");
        return;
      }
      const total =
        itemsRef.current.reduce((sum, a) => sum + a.size, 0) +
        list.reduce((sum, f) => sum + f.size, 0);
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
      commit([...itemsRef.current, ...added.map((a) => a.item)]);
      for (const { file, item } of added) {
        readBase64(file).then(
          (data) => commit(itemsRef.current.map((a) => (a.id === item.id ? { ...a, data } : a))),
          () => {
            commit(itemsRef.current.filter((a) => a.id !== item.id));
            setError("readFailed");
          },
        );
      }
    },
    [commit],
  );

  const remove = useCallback(
    (id: number) => {
      commit(itemsRef.current.filter((a) => a.id !== id));
      setError(null);
    },
    [commit],
  );

  /** Drops the given chips (the ones that just went out) and keeps anything
   * attached meanwhile. */
  const removeMany = useCallback(
    (ids: number[]) => {
      commit(itemsRef.current.filter((a) => !ids.includes(a.id)));
      setError(null);
    },
    [commit],
  );

  const ready = items.filter((a) => a.data !== null);
  const payload: ArticleAttachmentIn[] = ready.map((a) => ({
    filename: a.name,
    content_type: a.type,
    content_base64: a.data as string,
  }));

  return {
    items,
    error,
    encoding: items.some((a) => a.data === null),
    add,
    remove,
    removeMany,
    payload,
    /** Ids behind `payload`, same order. */
    payloadIds: ready.map((a) => a.id),
  };
}
