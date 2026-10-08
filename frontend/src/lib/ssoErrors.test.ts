import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { SSO_ERROR_CODES, ssoErrorKey } from "./ssoErrors";
import de from "@/i18n/locales/de.json";
import en from "@/i18n/locales/en.json";

describe("ssoErrorKey", () => {
  it("maps known codes and falls back", () => {
    expect(ssoErrorKey("no_match")).toBe("auth.ssoErrors.no_match");
    expect(ssoErrorKey("1")).toBe("auth.ssoFailed");
    expect(ssoErrorKey("true")).toBe("auth.ssoFailed");
    expect(ssoErrorKey("nonsense")).toBe("auth.ssoFailed");
    expect(ssoErrorKey(undefined)).toBeNull();
    expect(ssoErrorKey("")).toBeNull();
  });

  it("has a German and an English message for every code", () => {
    for (const code of SSO_ERROR_CODES) {
      expect(
        (de.auth.ssoErrors as Record<string, string>)[code],
        `de ${code}`,
      ).toBeTruthy();
      expect(
        (en.auth.ssoErrors as Record<string, string>)[code],
        `en ${code}`,
      ).toBeTruthy();
    }
  });

  it("mirrors SSO_ERROR_CODES of the backend", () => {
    const src = readFileSync(
      resolve(__dirname, "../../../backend/src/tiqora/identity/login.py"),
      "utf8",
    );
    const block = /RejectCode = Literal\[([\s\S]*?)\]/.exec(src)?.[1] ?? "";
    const reject = [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    const extra = [
      "unknown_provider",
      "config",
      "saml_unavailable",
      "state",
      "saml_invalid",
      "oidc_failed",
      "idp_error",
    ];
    expect([...SSO_ERROR_CODES].sort()).toEqual([...reject, ...extra].sort());
  });
});
