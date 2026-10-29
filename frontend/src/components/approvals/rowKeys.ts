import { useState } from "react";

let counter = 0;
/** A fresh key for a list row that has no identity of its own. */
export const newRowKey = (): number => ++counter;

/**
 * Stable React keys for the rows of a controlled list: rows keep their key (and their local state,
 * such as a search box) when siblings are removed or moved. The caller updates the keys together with
 * the list; a list that changes length behind our back (reset from outside) gets fresh keys.
 */
export function useRowKeys(length: number): [number[], (next: number[]) => void] {
  const [keys, setKeys] = useState<number[]>(() => Array.from({ length }, newRowKey));
  if (keys.length !== length) setKeys(Array.from({ length }, newRowKey));
  return [keys, setKeys];
}
