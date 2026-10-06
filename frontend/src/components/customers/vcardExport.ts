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
