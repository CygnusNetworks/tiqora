import { beforeAll, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { changeAdminErrorText, intOrNull } from "./changeAdminUi";

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

const t = i18n.t.bind(i18n);
const list = (...fields: string[]) =>
  new ApiError(422, { detail: fields.map((f) => ({ loc: ["body", ...f.split(".")], msg: "Field required", type: "missing" })) }, "/x");

describe("changeAdminErrorText", () => {
  it("names the first invalid field of a validation list and how many more there are", () => {
    expect(changeAdminErrorText(list("name"), t)).toBe("Die Eingabe ist ungültig (Feld: name).");
    expect(changeAdminErrorText(list("name", "rule"), t)).toBe("Die Eingabe ist ungültig (Feld: name und ein weiteres).");
    expect(changeAdminErrorText(list("name", "rule", "quorum"), t)).toBe("Die Eingabe ist ungültig (Feld: name und 2 weitere).");
  });

  it("labels a change error code with its numbers", () => {
    const err = new ApiError(422, { detail: { code: "cab_too_many_members", max: 50 } }, "/x");
    expect(changeAdminErrorText(err, t)).toBe("Ein CAB hat höchstens 50 Mitglieder.");
  });
});

describe("intOrNull", () => {
  it("reads whole numbers and leaves empty or partial input empty", () => {
    expect(intOrNull("-30")).toBe(-30);
    expect(intOrNull("")).toBeNull();
    expect(intOrNull("1.5")).toBeNull();
  });
});
