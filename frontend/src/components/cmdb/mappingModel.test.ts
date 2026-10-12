import { describe, expect, it } from "vitest";
import type { AttributeDef } from "@/lib/cmdbApi";
import {
  applyTemplate,
  countsOf,
  guessDelimiter,
  identifierOptions,
  importAllowed,
  parseCsv,
  parseReport,
  readCsvPreview,
  runFingerprint,
  suggestMapping,
  targetOptions,
  templateCsv,
  toMappingIn,
  validateMapping,
  type DryRun,
} from "./mappingModel";

const attr = (a: Partial<AttributeDef> & Pick<AttributeDef, "key" | "name" | "type">): AttributeDef =>
  ({ count_min: 0, count_max: 1, count_default: 1, required: false, searchable: false, portal: false, ...a }) as AttributeDef;

const DEFS: AttributeDef[] = [
  attr({ key: "Hostname", name: "Hostname", type: "text", searchable: true }),
  attr({ key: "Memory", name: "Arbeitsspeicher", type: "integer" }),
  attr({
    key: "NIC",
    name: "NIC",
    type: "group",
    count_max: 3,
    sub: [attr({ key: "IPAddress", name: "IP-Adresse", type: "ip", count_max: 2 }), attr({ key: "Label", name: "Bezeichnung", type: "text" })],
  }),
  attr({ key: "Files", name: "Dateien", type: "attachment" }),
  attr({ key: "Owner", name: "Eigentümer", type: "text" }),
  attr({ key: "Contact", name: "Eigentümer", type: "text" }),
];

describe("targetOptions", () => {
  const opts = targetOptions(DEFS);
  const values = opts.map((o) => o.value);

  it("lists every leaf with positions up to count_max and a readable label", () => {
    expect(values).toContain("attr:Hostname");
    expect(values).toContain("attr:NIC.IPAddress");
    expect(values).toContain("attr:NIC.IPAddress[2]");
    expect(values).toContain("attr:NIC[2].IPAddress[2]");
    expect(values).toContain("attr:NIC[3].Label");
    expect(values).not.toContain("attr:NIC[4].Label");
    expect(opts.find((o) => o.value === "attr:NIC[2].IPAddress")?.label).toBe("NIC 2 › IP-Adresse 1");
    expect(opts.find((o) => o.value === "attr:Hostname")?.label).toBe("Hostname");
  });

  it("leaves out groups and attachments", () => {
    expect(values).not.toContain("attr:NIC");
    expect(values).not.toContain("attr:Files");
  });
});

describe("suggestMapping", () => {
  it("maps the header of an export 1:1", () => {
    const header = ["number", "name", "class", "deployment_state", "incident_state", "attr:Hostname", "attr:NIC.IPAddress", "attr:NIC[2].IPAddress[2]"];
    const rows = suggestMapping(header, DEFS);
    expect(rows.map((r) => r.column)).toEqual(header);
    expect(rows.map((r) => r.target)).toEqual([
      "number",
      "name",
      "", // the class column is no target
      "deployment_state",
      "incident_state",
      "attr:Hostname",
      "attr:NIC.IPAddress",
      "attr:NIC[2].IPAddress[2]",
    ]);
  });

  it("recognises German and English names of the fixed fields", () => {
    const rows = suggestMapping(["Nummer", "Name", "Verwendungsstatus", "Vorfallsstatus"], DEFS);
    expect(rows.map((r) => r.target)).toEqual(["number", "name", "deployment_state", "incident_state"]);
  });

  it("matches attributes by key or label, ignoring case", () => {
    const rows = suggestMapping(["hostname", "ARBEITSSPEICHER", "IP-Adresse"], DEFS);
    expect(rows.map((r) => r.target)).toEqual(["attr:Hostname", "attr:Memory", "attr:NIC.IPAddress"]);
  });

  it("leaves an ambiguous label and an unknown header unmapped", () => {
    const rows = suggestMapping(["Eigentümer", "Whatever", ""], DEFS);
    expect(rows.map((r) => r.target)).toEqual(["", "", ""]);
  });

  it("does not suggest a target twice or an attribute that does not exist", () => {
    const rows = suggestMapping(["attr:Hostname", "Hostname", "attr:Nope", "attr:NIC[4].Label"], DEFS);
    expect(rows.map((r) => r.target)).toEqual(["attr:Hostname", "", "", ""]);
  });
});

describe("validateMapping", () => {
  const ok = [
    { column: "A", target: "number" },
    { column: "B", target: "name" },
  ];

  it("accepts a complete mapping", () => {
    expect(validateMapping(ok, ["number"])).toEqual([]);
  });

  it("flags nothing mapped, duplicates, an unmapped identifier and a missing name", () => {
    expect(validateMapping([{ column: "A", target: "" }], ["number"])).toContain("cmdb.import.problem.nothingMapped");
    expect(validateMapping([...ok, { column: "C", target: "name" }], ["number"])).toContain("cmdb.import.problem.duplicate");
    expect(validateMapping(ok, ["attr:Hostname"])).toContain("cmdb.import.problem.identifierUnmapped");
    expect(validateMapping([{ column: "A", target: "number" }], ["number"])).toContain("cmdb.import.problem.nameMissing");
    expect(validateMapping([{ column: "A", target: "number" }], ["number"], { createMissing: false })).toEqual([]);
    expect(validateMapping(ok, [])).toContain("cmdb.import.problem.noIdentifier");
  });

  it("treats [1] as optional when comparing targets", () => {
    const rows = [
      { column: "A", target: "attr:NIC[1].IPAddress" },
      { column: "B", target: "attr:NIC.IPAddress" },
      { column: "C", target: "name" },
    ];
    expect(validateMapping(rows, ["attr:NIC.IPAddress"])).toEqual(["cmdb.import.problem.duplicate"]);
  });
});

describe("templates", () => {
  it("round-trips a mapping through the stored form", () => {
    const header = ["Nr", "Bezeichnung", "Notiz"];
    const rows = [
      { column: "Nr", target: "number" },
      { column: "Bezeichnung", target: "name" },
      { column: "Notiz", target: "" },
    ];
    const stored = toMappingIn(rows);
    expect(stored).toEqual([
      { column: "Nr", target: "number" },
      { column: "Bezeichnung", target: "name" },
      { column: "Notiz", target: "ignore" },
    ]);
    expect(applyTemplate(header, stored)).toEqual(rows);
  });

  it("applies a template to a file with other column order and case, unknown columns stay unmapped", () => {
    const stored = [
      { column: "Nr", target: "number" },
      { column: "Name", target: "name" },
    ];
    expect(applyTemplate(["name", "Extra", "NR"], stored)).toEqual([
      { column: "name", target: "name" },
      { column: "Extra", target: "" },
      { column: "NR", target: "number" },
    ]);
  });
});

describe("identifierOptions", () => {
  it("offers number, name and the searchable attributes", () => {
    expect(identifierOptions(DEFS)).toEqual(["number", "name", "attr:Hostname"]);
  });
});

describe("csv", () => {
  it("parses quotes, doubled quotes, embedded newlines and CRLF", () => {
    const rows = parseCsv('a;b\r\n"x;1";"he said ""hi"""\r\n"two\nlines";z', ";");
    expect(rows).toEqual([
      ["a", "b"],
      ["x;1", 'he said "hi"'],
      ["two\nlines", "z"],
    ]);
  });

  it("guesses the delimiter from the first line", () => {
    expect(guessDelimiter("a,b,c\n1;2;3")).toBe(",");
    expect(guessDelimiter("﻿a;b;c")).toBe(";");
    expect(guessDelimiter("a\tb\tc")).toBe("\t");
  });

  const enc = (s: string) => new TextEncoder().encode(s).buffer as ArrayBuffer;

  it("reads header and the first five rows as per-column samples", () => {
    const text = ["number;name;attr:Hostname", ...Array.from({ length: 8 }, (_, i) => `SRV-${i};n${i};h${i}`)].join("\n");
    const p = readCsvPreview(enc(text), "utf-8", ";");
    expect(p.header).toEqual(["number", "name", "attr:Hostname"]);
    expect(p.samples[0]).toEqual(["SRV-0", "SRV-1", "SRV-2", "SRV-3", "SRV-4"]);
  });

  it("strips the export's formula guard in samples and reports an undecodable file", () => {
    expect(readCsvPreview(enc("a\n'=SUM(A1)"), "utf-8", ";").samples[0]).toEqual(["=SUM(A1)"]);
    const latin1 = new Uint8Array([0x61, 0x0a, 0xfc]).buffer; // "a\nü" in cp1252
    expect(readCsvPreview(latin1, "utf-8", ";").error).toBe("decode");
    expect(readCsvPreview(latin1, "cp1252", ";").samples[0]).toEqual(["ü"]);
    expect(readCsvPreview(enc(""), "utf-8", ";").error).toBe("empty");
  });

  it("builds a header-only template with the class's columns", () => {
    const text = templateCsv(DEFS);
    expect(text.startsWith("﻿number;name;deployment_state;incident_state;attr:Hostname;")).toBe(true);
    expect(text).toContain(";attr:NIC[3].Label");
    expect(text.endsWith("\r\n")).toBe(true);
    expect(text.trim().split("\r\n")).toHaveLength(1);
  });
});

describe("dry-run gate", () => {
  const fp = runFingerprint({ file: { name: "a.csv", size: 10, lastModified: 1 }, classId: 1, format: "csv", options: { on_error: "abort" }, mapping: [{ column: "A", target: "name" }] });
  const dry = (errors: number, fingerprint = fp): DryRun => ({ fingerprint, counts: { created: 1, updated: 0, unchanged: 0, errors, skipped: 0 } });

  it("needs a dry run", () => {
    expect(importAllowed(null, fp, "abort")).toBe(false);
  });

  it("allows a clean dry run of the same file and mapping", () => {
    expect(importAllowed(dry(0), fp, "abort")).toBe(true);
  });

  it("blocks errors unless faulty rows are skipped", () => {
    expect(importAllowed(dry(2), fp, "abort")).toBe(false);
    expect(importAllowed(dry(2), fp, "skip")).toBe(true);
  });

  it("blocks when the mapping or the file changed after the dry run", () => {
    const other = runFingerprint({ file: { name: "a.csv", size: 10, lastModified: 1 }, classId: 1, format: "csv", options: { on_error: "abort" }, mapping: [{ column: "A", target: "number" }] });
    expect(importAllowed(dry(0), other, "abort")).toBe(false);
    const otherFile = runFingerprint({ file: { name: "a.csv", size: 11, lastModified: 1 }, classId: 1, format: "csv", options: { on_error: "abort" }, mapping: [{ column: "A", target: "name" }] });
    expect(importAllowed(dry(0), otherFile, "abort")).toBe(false);
  });
});

describe("job result and report", () => {
  it("reads the counts and defaults missing ones to 0", () => {
    expect(countsOf({ created: 3, updated: 1, errors: 2, dry_run: true })).toEqual({ created: 3, updated: 1, unchanged: 0, errors: 2, skipped: 0 });
    expect(countsOf(null).created).toBe(0);
  });

  it("parses the first rows of report.csv", () => {
    const text = "﻿row;status;ci_number;message\r\n2;created;SRV-1;\r\n3;error;;'=bad: Wert\r\n";
    expect(parseReport(text)).toEqual([
      { row: "2", status: "created", ciNumber: "SRV-1", message: "" },
      { row: "3", status: "error", ciNumber: "", message: "=bad: Wert" },
    ]);
    const long = "row;status;ci_number;message\r\n" + Array.from({ length: 150 }, (_, i) => `${i};created;;`).join("\r\n");
    expect(parseReport(long, 100)).toHaveLength(100);
  });
});
