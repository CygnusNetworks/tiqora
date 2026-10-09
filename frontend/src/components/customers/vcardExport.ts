import type { DragEvent } from "react";
import { api } from "@/lib/api";
import { downloadText } from "@/lib/cryptoFiles";

/** Most contacts per vCard export (mirrors the backend's EXPORT_MAX). */
export const VCARD_EXPORT_MAX = 1000;

/** Fetch the selected contacts as one .vcf and offer it as a download. */
export async function saveSelectedVcards(logins: string[]): Promise<void> {
  const text = await api.exportCustomerVcards(logins);
  downloadText("kontakte.vcf", text, "text/vcard;charset=utf-8");
}

/** Start a file download from a same-origin URL (session-cookie auth). */
export function downloadUrl(url: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** "Jane Doe" → "Jane Doe.vcf"; drops what a DownloadURL or a file name cannot carry. */
export function vcfFileName(name: string): string {
  const base = name.replace(/[:/\\\n\r"]+/g, " ").replace(/\s+/g, " ").trim();
  return `${base || "kontakt"}.vcf`;
}

/**
 * Makes a vCard download link draggable out of the browser — onto the desktop,
 * a Finder window or Apple Contacts. Chromium browsers (Chrome, Edge, Arc)
 * turn the `DownloadURL` drag into the .vcf file; Safari and Firefox only
 * carry the link, there a click downloads the card.
 */
export function vcardDragProps(url: string, name: string) {
  return {
    draggable: true,
    onDragStart: (e: DragEvent<HTMLElement>) => {
      const href = new URL(url, window.location.href).href;
      e.dataTransfer.effectAllowed = "copy";
      e.dataTransfer.setData("DownloadURL", `text/vcard:${vcfFileName(name)}:${href}`);
      e.dataTransfer.setData("text/uri-list", href);
      e.dataTransfer.setData("text/plain", href);
    },
  };
}
