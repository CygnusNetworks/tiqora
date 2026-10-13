/**
 * Pure model of the CMDB import wizard (D9): column suggestions, target options, validation, the
 * small client-side CSV reader for the samples, and the dry-run gate. No React, no network.
 *
 * Targets use the server's syntax (`backend/src/tiqora/cmdb/transfer/mapping.py`): `number`, `name`,
 * `deployment_state`, `incident_state`, `attr:NIC[2].IPAddress` (`[1]` may be left out). An empty
 * target means "do not import this column" and is sent as `ignore`.
 */
import type { AttributeDef } from "@/lib/cmdbApi";

export type MappingRow = { column: string; target: string };
export type TargetOption = { value: string; label: string };
export type MappingRowIn = { column: string | number; target: string };

export const FIXED_TARGETS = ["number", "name", "deployment_state", "incident_state"] as const;
export type FixedTarget = (typeof FIXED_TARGETS)[number];
export const IDENTIFIER_FIXED: readonly FixedTarget[] = ["number", "name"];

/** Entries offered per repeatable level (the export writes at most this many, too). */
export const OPTION_ENTRY_CAP = 10;
/** The same bound the server uses for the preview of a CSV file in the wizard. */
export const SAMPLE_ROWS = 5;
export const REPORT_ROWS = 100;
export const REPORT_NAME = "report.csv";
export const INPUT_FILE_PREFIX = "input-";

const FORMULA_PREFIXES = ["=", "+", "-", "@", "\t", "\r"];

const FIXED_ALIASES: Record<FixedTarget, string[]> = {
  number: ["number", "nummer", "ci-nummer", "ci nummer", "ci number"],
  name: ["name", "bezeichnung"],
  deployment_state: ["deployment_state", "deployment state", "verwendungsstatus", "deploymentstate"],
  incident_state: ["incident_state", "incident state", "vorfallsstatus", "incidentstate"],
};

const fold = (s: string) => s.trim().toLocaleLowerCase("de");

/** `attr:NIC[1].IP[1]` and `attr:NIC.IP` are the same target. */
export function normalizeTarget(target: string): string {
  return target.trim().replace(/\[1\]/g, "");
}

type Leaf = { path: { key: string; name: string; countMax: number }[]; def: AttributeDef };

function leavesOf(defs: AttributeDef[]): Leaf[] {
  const out: Leaf[] = [];
  const walk = (items: AttributeDef[], prefix: Leaf["path"]) => {
    for (const d of items) {
      const path = [...prefix, { key: d.key, name: d.name, countMax: d.count_max }];
      const sub = d.sub ?? [];
      if (d.type === "group" || sub.length > 0) walk(sub, path);
      else if (d.type !== "attachment") out.push({ path, def: d });
    }
  };
  walk(defs, []);
  return out;
}

function formatPath(path: { key: string }[], positions: number[]): string {
  return "attr:" + path.map((p, i) => (positions[i] === 1 ? p.key : `${p.key}[${positions[i]}]`)).join(".");
}

/** Every importable leaf attribute: position 1 and, for repeatable levels, up to `count_max` entries. */
export function targetOptions(defs: AttributeDef[]): TargetOption[] {
  const out: TargetOption[] = [];
  for (const leaf of leavesOf(defs)) {
    const rec = (level: number, positions: number[]) => {
      if (level === leaf.path.length) {
        const label = leaf.path
          .map((p, i) => (p.countMax > 1 ? `${p.name} ${positions[i]}` : p.name))
          .join(" › ");
        out.push({ value: formatPath(leaf.path, positions), label });
        return;
      }
      const max = Math.max(1, Math.min(leaf.path[level].countMax, OPTION_ENTRY_CAP));
      for (let i = 1; i <= max; i += 1) rec(level + 1, [...positions, i]);
    };
    rec(0, []);
  }
  return out;
}

/** Does `attr:…` name an importable leaf of the definition (positions within `count_max`)? */
function attrTargetExists(target: string, leaves: Leaf[]): boolean {
  if (!target.startsWith("attr:")) return false;
  const segments = target.slice("attr:".length).split(".");
  return leaves.some((leaf) => {
    if (leaf.path.length !== segments.length) return false;
    return segments.every((seg, i) => {
      const m = /^([^[\]]+)(?:\[(\d+)\])?$/.exec(seg);
      if (!m) return false;
      const pos = m[2] === undefined ? 1 : Number(m[2]);
      return m[1] === leaf.path[i].key && pos >= 1 && pos <= Math.max(1, leaf.path[i].countMax);
    });
  });
}

/**
 * Suggested target per column. A header that is itself a target (the CMDB export) maps 1:1;
 * otherwise the fixed fields are recognised by their German or English name, and attributes by
 * key or label (case-insensitive) when exactly one attribute fits. Anything ambiguous stays
 * unmapped, and a target is suggested at most once.
 */
export function suggestMapping(header: string[], defs: AttributeDef[]): MappingRow[] {
  const leaves = leavesOf(defs);
  const used = new Set<string>();
  return header.map((column) => {
    const take = (target: string): MappingRow => {
      const key = normalizeTarget(target);
      if (used.has(key)) return { column, target: "" };
      used.add(key);
      return { column, target: key };
    };
    const raw = column.trim();
    if ((FIXED_TARGETS as readonly string[]).includes(raw)) return take(raw);
    if (attrTargetExists(normalizeTarget(raw), leaves)) return take(raw);

    const f = fold(raw);
    for (const fixed of FIXED_TARGETS) {
      if (FIXED_ALIASES[fixed].includes(f)) return take(fixed);
    }
    if (f === "") return { column, target: "" };
    const hits = leaves.filter((l) => {
      const last = l.path[l.path.length - 1];
      return fold(last.key) === f || fold(last.name) === f;
    });
    if (hits.length === 1) return take(formatPath(hits[0].path, hits[0].path.map(() => 1)));
    return { column, target: "" };
  });
}

/**
 * Problems that keep the wizard from starting a dry run, as i18n keys (`cmdb.import.problem.*`):
 * nothing mapped, a target used twice, an identifier no column fills, and (for new CIs) no name.
 */
export function validateMapping(
  rows: MappingRow[],
  identifiers: string[],
  opts: { createMissing?: boolean } = {},
): string[] {
  const problems = new Set<string>();
  const mapped = new Map<string, number>();
  for (const r of rows) {
    if (r.target === "") continue;
    const key = normalizeTarget(r.target);
    mapped.set(key, (mapped.get(key) ?? 0) + 1);
  }
  if (mapped.size === 0) problems.add("cmdb.import.problem.nothingMapped");
  for (const n of mapped.values()) if (n > 1) problems.add("cmdb.import.problem.duplicate");
  for (const id of identifiers) {
    if (!mapped.has(normalizeTarget(id))) problems.add("cmdb.import.problem.identifierUnmapped");
  }
  if (identifiers.length === 0) problems.add("cmdb.import.problem.noIdentifier");
  if (opts.createMissing !== false && !mapped.has("name")) problems.add("cmdb.import.problem.nameMissing");
  return [...problems];
}

/** The rows as the API takes them (`ignore` for an unmapped column). */
export function toMappingIn(rows: MappingRow[]): MappingRowIn[] {
  return rows.map((r) => ({ column: r.column, target: r.target === "" ? "ignore" : r.target }));
}

/** Rows for a file header from a stored mapping: columns are matched by header text, ignoring case. */
export function applyTemplate(header: string[], stored: MappingRowIn[]): MappingRow[] {
  const byColumn = new Map<string, string>();
  for (const s of stored) byColumn.set(fold(String(s.column)), s.target);
  return header.map((column) => {
    const target = byColumn.get(fold(column));
    return { column, target: target === undefined || target === "ignore" ? "" : normalizeTarget(target) };
  });
}

/** Identifier choices: number, name and the searchable leaf attributes. */
export function identifierOptions(defs: AttributeDef[]): string[] {
  const out: string[] = [...IDENTIFIER_FIXED];
  for (const leaf of leavesOf(defs)) {
    if (!leaf.def.searchable) continue;
    out.push(formatPath(leaf.path, leaf.path.map(() => 1)));
  }
  return out;
}

// --- client-side CSV ----------------------------------------------------------------------------

export type CsvDelimiter = ";" | "," | "\t" | "|";
export type CsvEncoding = "utf-8" | "cp1252";

/** RFC 4180 reader (quoted fields, doubled quotes, newlines inside quotes). */
export function parseCsv(text: string, delimiter: CsvDelimiter, maxRows = Infinity): string[][] {
  const src = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let i = 0;
  const endRow = () => {
    row.push(field);
    field = "";
    rows.push(row);
    row = [];
  };
  while (i < src.length && rows.length < maxRows) {
    const ch = src[i];
    if (quoted) {
      if (ch === '"') {
        if (src[i + 1] === '"') {
          field += '"';
          i += 1;
        } else quoted = false;
      } else field += ch;
    } else if (ch === '"' && field === "") quoted = true;
    else if (ch === delimiter) {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && src[i + 1] === "\n") i += 1;
      endRow();
    } else field += ch;
    i += 1;
  }
  if (rows.length < maxRows && (field !== "" || row.length > 0)) endRow();
  return rows;
}

/** Most frequent candidate delimiter in the first line. */
export function guessDelimiter(text: string): CsvDelimiter {
  const first = (text.charCodeAt(0) === 0xfeff ? text.slice(1) : text).split(/\r?\n/, 1)[0] ?? "";
  const candidates: CsvDelimiter[] = [";", ",", "\t", "|"];
  let best: CsvDelimiter = ";";
  let bestCount = 0;
  for (const c of candidates) {
    const n = first.split(c).length - 1;
    if (n > bestCount) {
      best = c;
      bestCount = n;
    }
  }
  return best;
}

/** Exactly the guard the export writes (`'=…`); the server strips it on import. */
export function unguard(text: string): string {
  return text.length > 1 && text[0] === "'" && FORMULA_PREFIXES.includes(text[1]) ? text.slice(1) : text;
}

export type CsvPreview = { header: string[]; samples: string[][]; error?: "decode" | "empty" };

/** Header and the first `SAMPLE_ROWS` rows of a CSV file; samples are per column. */
export function readCsvPreview(buffer: ArrayBuffer, encoding: CsvEncoding, delimiter: CsvDelimiter): CsvPreview {
  let text: string;
  try {
    text = new TextDecoder(encoding === "cp1252" ? "windows-1252" : "utf-8", { fatal: true }).decode(buffer);
  } catch {
    return { header: [], samples: [], error: "decode" };
  }
  const rows = parseCsv(text, delimiter, SAMPLE_ROWS + 1);
  if (rows.length === 0) return { header: [], samples: [], error: "empty" };
  const header = rows[0].map((h) => h.trim());
  const samples = header.map((_, col) =>
    rows
      .slice(1)
      .map((r) => unguard(r[col] ?? ""))
      .filter((v) => v !== ""),
  );
  return { header, samples };
}

/** Header-only template file for a class: the fixed columns and every attribute target. */
export function templateCsv(defs: AttributeDef[], delimiter: CsvDelimiter = ";"): string {
  const cols = [...FIXED_TARGETS, ...targetOptions(defs).map((o) => o.value)];
  const quote = (v: string) => (v.includes(delimiter) || v.includes('"') ? `"${v.replace(/"/g, '""')}"` : v);
  return "﻿" + cols.map(quote).join(delimiter) + "\r\n";
}

// --- dry run and report -------------------------------------------------------------------------

export type ImportCounts = { created: number; updated: number; unchanged: number; errors: number; skipped: number };

export function countsOf(result: Record<string, unknown> | null | undefined): ImportCounts {
  const n = (k: string) => (typeof result?.[k] === "number" ? (result[k] as number) : 0);
  return { created: n("created"), updated: n("updated"), unchanged: n("unchanged"), errors: n("errors"), skipped: n("skipped") };
}

export type DryRun = { fingerprint: string; counts: ImportCounts };

/** What identifies "the same file and mapping": a change to either invalidates the dry run. */
export function runFingerprint(parts: {
  file: { name: string; size: number; lastModified: number } | null;
  classId: number | null;
  format: "csv" | "xlsx";
  options: unknown;
  mapping: MappingRowIn[];
}): string {
  return JSON.stringify(parts);
}

/** The real import needs a dry run of the same file and mapping that either had no errors or runs with `on_error=skip`. */
export function importAllowed(dry: DryRun | null, fingerprint: string, onError: "abort" | "skip"): boolean {
  if (!dry || dry.fingerprint !== fingerprint) return false;
  return dry.counts.errors === 0 || onError === "skip";
}

export type ReportRow = { row: string; status: string; ciNumber: string; message: string };

/** First rows of the job's `report.csv` (`row;status;ci_number;message`). */
export function parseReport(text: string, limit = REPORT_ROWS): ReportRow[] {
  return parseCsv(text, ";", limit + 1)
    .slice(1)
    .map((r) => ({ row: r[0] ?? "", status: r[1] ?? "", ciNumber: unguard(r[2] ?? ""), message: unguard(r[3] ?? "") }));
}
