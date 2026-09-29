/**
 * File helpers for the PGP / S-MIME admin pages: read uploads as text or
 * base64 (DER certificates) and hand downloaded key material to the browser.
 */

/** Read a File as UTF-8 text (armored keys, PEM). */
export function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsText(file);
  });
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

/**
 * Certificate upload payload: PEM files as text, anything binary (DER
 * `.cer`/`.der`) as base64 — the API accepts both.
 */
export async function readCertificateFile(file: File): Promise<string> {
  const buf = new Uint8Array(await file.arrayBuffer());
  const head = new TextDecoder("utf-8", { fatal: false }).decode(buf.subarray(0, 4096));
  if (head.includes("-----BEGIN")) {
    return new TextDecoder("utf-8").decode(buf);
  }
  return bytesToBase64(buf);
}

/** Offer *content* as a file download. */
export function downloadText(filename: string, content: string, type: string): void {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
