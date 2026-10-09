import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SsoProviderButtons } from "./SsoProviderButtons";

function stubLocationAssign() {
  const original = window.location;
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign },
  });
  return {
    assign,
    restore: () =>
      Object.defineProperty(window, "location", {
        configurable: true,
        value: original,
      }),
  };
}

const providers = [
  { slug: "entra", kind: "oidc" as const, label: "Mit Microsoft anmelden", icon: "microsoft" },
  { slug: "adfs", kind: "saml" as const, label: "Firmenkonto", icon: "building" },
  { slug: "env-oidc", kind: "oidc" as const, label: "", icon: null },
];

describe("SsoProviderButtons", () => {
  let restore: (() => void) | null = null;
  afterEach(() => restore?.());

  function renderButtons(onBeforeNavigate?: (p: unknown) => void) {
    return render(
      <I18nextProvider i18n={i18n}>
        <SsoProviderButtons
          providers={providers}
          hrefFor={(s) => `/x/${s}`}
          onBeforeNavigate={onBeforeNavigate}
        />
      </I18nextProvider>,
    );
  }

  it("renders one button per provider and navigates to its login URL", () => {
    const loc = stubLocationAssign();
    restore = loc.restore;
    const before = vi.fn();
    renderButtons(before);
    expect(screen.getByTestId("sso-provider-entra")).toHaveTextContent(
      "Mit Microsoft anmelden",
    );
    fireEvent.click(screen.getByTestId("sso-provider-adfs"));
    expect(before).toHaveBeenCalledWith(expect.objectContaining({ slug: "adfs" }));
    expect(loc.assign).toHaveBeenCalledWith("/x/adfs");
  });

  it("falls back to the default label when the label is empty", async () => {
    await i18n.changeLanguage("de");
    renderButtons();
    expect(screen.getByTestId("sso-provider-env-oidc")).toHaveTextContent(
      "Mit SSO anmelden",
    );
  });

  it("renders nothing without providers", () => {
    const { container } = render(
      <I18nextProvider i18n={i18n}>
        <SsoProviderButtons providers={[]} hrefFor={(s) => s} />
      </I18nextProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
