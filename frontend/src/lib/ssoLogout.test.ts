import { describe, it, expect, vi, afterEach } from "vitest";
import { submitSsoLogout } from "./ssoLogout";

describe("submitSsoLogout", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  it("posts a form to the given action", () => {
    const submit = vi
      .spyOn(HTMLFormElement.prototype, "submit")
      .mockImplementation(() => {});
    submitSsoLogout("/api/v1/auth/sso/logout");
    const form = document.querySelector(
      "form[action='/api/v1/auth/sso/logout']",
    ) as HTMLFormElement;
    expect(form).not.toBeNull();
    expect(form.method).toBe("post");
    expect(submit).toHaveBeenCalled();
  });
});
