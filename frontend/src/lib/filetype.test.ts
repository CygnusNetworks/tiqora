import { describe, it, expect } from "vitest";
import { fileTypeInfo } from "./filetype";

describe("fileTypeInfo", () => {
  it.each([
    ["application/pdf", "scan.pdf", "PDF"],
    ['application/pdf; name="Netzwerkmentorin_Dana.pdf"', null, "PDF"],
    ["application/vnd.openxmlformats-officedocument.wordprocessingml.document", "brief.docx", "DOC"],
    ["application/msword", null, "DOC"],
    ["application/vnd.oasis.opendocument.text", null, "DOC"],
    ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", null, "XLS"],
    ["text/csv", null, "CSV"],
    ["image/png", "logo.png", "IMG"],
    ["application/zip", null, "ZIP"],
    ["message/rfc822", "fwd.eml", "MAIL"],
    ["text/calendar", null, "ICS"],
    ["text/html; charset=utf-8", null, "HTML"],
    ["text/plain", "notes.txt", "TXT"],
  ])("maps %s to %s", (mime, name, label) => {
    expect(fileTypeInfo(mime, name).label).toBe(label);
  });

  it("falls back to the extension for unknown MIME types", () => {
    expect(fileTypeInfo("application/octet-stream", "backup.bak").label).toBe("BAK");
  });

  it("falls back to FILE when nothing is known", () => {
    expect(fileTypeInfo(null, null).label).toBe("FILE");
    expect(fileTypeInfo("application/octet-stream", "no-extension").label).toBe("FILE");
  });
});
