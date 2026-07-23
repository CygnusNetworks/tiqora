/** Human file-type badge for attachments: maps MIME type (with filename
 * extension as fallback) to a short label + badge color classes, so lists
 * never show raw strings like `application/pdf; name="…"`. */

export type FileTypeInfo = {
  /** Short uppercase label, e.g. "PDF", "DOC", "IMG". */
  label: string;
  /** Tailwind classes for the badge chip (bg + text). */
  className: string;
};

const TONE = {
  red: "bg-danger/15 text-danger",
  blue: "bg-accent-dim text-accent",
  green: "bg-green/15 text-green",
  amber: "bg-escalation/15 text-escalation",
  muted: "bg-surface-subtle text-muted",
} as const;

type Rule = { test: (mime: string, ext: string) => boolean; label: string; tone: keyof typeof TONE };

const RULES: Rule[] = [
  { test: (m) => m.includes("pdf"), label: "PDF", tone: "red" },
  {
    test: (m, e) => m.includes("msword") || m.includes("wordprocessingml") || m.includes("opendocument.text") || e === "doc" || e === "docx" || e === "odt",
    label: "DOC",
    tone: "blue",
  },
  {
    test: (m, e) => m.includes("excel") || m.includes("spreadsheetml") || m.includes("opendocument.spreadsheet") || e === "xls" || e === "xlsx" || e === "ods",
    label: "XLS",
    tone: "green",
  },
  {
    test: (m, e) => m.includes("powerpoint") || m.includes("presentationml") || m.includes("opendocument.presentation") || e === "ppt" || e === "pptx" || e === "odp",
    label: "PPT",
    tone: "amber",
  },
  { test: (m, e) => m.includes("csv") || e === "csv", label: "CSV", tone: "green" },
  { test: (m) => m.startsWith("image/"), label: "IMG", tone: "blue" },
  { test: (m) => m.startsWith("audio/"), label: "AUDIO", tone: "amber" },
  { test: (m) => m.startsWith("video/"), label: "VIDEO", tone: "amber" },
  {
    test: (m, e) => m.includes("zip") || m.includes("compressed") || m.includes("x-tar") || m.includes("gzip") || e === "7z" || e === "rar",
    label: "ZIP",
    tone: "amber",
  },
  { test: (m, e) => m.includes("rfc822") || e === "eml" || e === "msg", label: "MAIL", tone: "muted" },
  { test: (m, e) => m.includes("calendar") || e === "ics", label: "ICS", tone: "blue" },
  { test: (m) => m.includes("html"), label: "HTML", tone: "muted" },
  { test: (m) => m.startsWith("text/"), label: "TXT", tone: "muted" },
];

export function fileTypeInfo(
  contentType: string | null | undefined,
  filename: string | null | undefined,
): FileTypeInfo {
  const mime = (contentType ?? "").split(";")[0].trim().toLowerCase();
  const name = (filename ?? "").toLowerCase();
  const dot = name.lastIndexOf(".");
  const ext = dot >= 0 ? name.slice(dot + 1) : "";

  for (const rule of RULES) {
    if (rule.test(mime, ext)) return { label: rule.label, className: TONE[rule.tone] };
  }
  // Unknown type: a short extension still tells the human more than "FILE".
  if (ext && ext.length <= 4 && /^[a-z0-9]+$/.test(ext)) {
    return { label: ext.toUpperCase(), className: TONE.muted };
  }
  return { label: "FILE", className: TONE.muted };
}
