import { describe, it, expect } from "vitest";
import { isSessionlessPath } from "./api";

describe("isSessionlessPath", () => {
  it.each(["/login", "/login?next=%2Fagent", "/wallboard", "/approve", "/approve?x=1", "/approve/"])(
    "%s never redirects to login",
    (path) => expect(isSessionlessPath(path)).toBe(true),
  );
  it.each(["/agent", "/approvals", "/approvals/12", "/approved", "/agent/approvals"])(
    "%s still redirects to login",
    (path) => expect(isSessionlessPath(path)).toBe(false),
  );
});
