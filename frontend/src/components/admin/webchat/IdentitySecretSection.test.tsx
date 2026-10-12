import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { useState } from "react";
import i18n from "@/i18n";
import { IdentitySecretSection } from "./IdentitySecretSection";
import { IDENTITY_SAMPLES } from "./identitySamples";

const create = vi.fn();
const remove = vi.fn();
vi.mock("@/lib/webchatApi", async (orig) => {
  const mod = await orig<typeof import("@/lib/webchatApi")>();
  return {
    ...mod,
    webchatApi: {
      createIdentitySecret: (...a: unknown[]) => create(...a),
      deleteIdentitySecret: (...a: unknown[]) => remove(...a),
    },
  };
});

const changed = vi.fn();
function Harness({ initial }: { initial: boolean }) {
  const [isSet, setIsSet] = useState(initial);
  return (
    <IdentitySecretSection
      configId={7}
      isSet={isSet}
      onChanged={(v) => {
        changed(v);
        setIsSet(v);
      }}
    />
  );
}
function renderSection(initial: boolean) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <Harness initial={initial} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("IdentitySecretSection", () => {
  beforeAll(() => i18n.changeLanguage("de"));
  afterAll(() => i18n.changeLanguage("en"));
  beforeEach(() => vi.clearAllMocks());

  it("creates a key and shows it once with the warning and a copy field", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    create.mockResolvedValue({ secret: "s3cret-value" });
    renderSection(false);
    expect(screen.getByTestId("webchat-identity-state")).toHaveTextContent("Es gibt noch keinen Schlüssel.");

    fireEvent.click(screen.getByTestId("webchat-identity-create"));
    const field = await screen.findByTestId("webchat-identity-secret");
    expect(field).toHaveValue("s3cret-value");
    expect(field).toHaveAttribute("readonly");
    expect(screen.getByTestId("webchat-identity-warning")).toHaveTextContent("Nur jetzt sichtbar");
    expect(create).toHaveBeenCalledWith(7);
    expect(changed).toHaveBeenCalledWith(true);

    fireEvent.click(screen.getByTestId("webchat-identity-copy"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("s3cret-value"));
    expect(await screen.findByText("Kopiert.")).toBeInTheDocument();
  });

  it("asks before replacing the key and only then creates the new one", async () => {
    create.mockResolvedValue({ secret: "neu-1" });
    renderSection(true);
    expect(screen.getByTestId("webchat-identity-state")).toHaveTextContent("wird er nicht erneut angezeigt");
    expect(screen.queryByTestId("webchat-identity-secret")).toBeNull();

    fireEvent.click(screen.getByTestId("webchat-identity-regenerate"));
    expect(
      await screen.findByText(
        "Bestehende Anmeldungen auf Ihrer Website funktionieren erst wieder, wenn Sie den neuen Schlüssel dort eintragen.",
      ),
    ).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    await waitFor(() => expect(screen.queryByTestId("confirm-dialog-cancel")).toBeNull());
    expect(create).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("webchat-identity-regenerate"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    expect(await screen.findByTestId("webchat-identity-secret")).toHaveValue("neu-1");
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("asks before removing the key", async () => {
    remove.mockResolvedValue(undefined);
    renderSection(true);
    fireEvent.click(screen.getByTestId("webchat-identity-remove"));
    await screen.findByTestId("confirm-dialog-confirm");
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(7));
    await waitFor(() => expect(changed).toHaveBeenCalledWith(false));
    expect(await screen.findByTestId("webchat-identity-create")).toBeInTheDocument();
  });

  it("shows an error when the key cannot be created", async () => {
    create.mockRejectedValue(new Error("down"));
    renderSection(false);
    fireEvent.click(screen.getByTestId("webchat-identity-create"));
    expect(await screen.findByTestId("webchat-identity-error")).toBeInTheDocument();
    expect(screen.queryByTestId("webchat-identity-secret")).toBeNull();
  });

  it("offers signing samples for Node, PHP and Python using example.org and HS256", () => {
    renderSection(false);
    const sample = () => screen.getByTestId("webchat-identity-sample").textContent ?? "";
    expect(sample()).toContain("createHmac(\"sha256\"");
    for (const [lang, marker] of [
      ["php", "hash_hmac('sha256'"],
      ["python", "hashlib.sha256"],
      ["node", "createHmac"],
    ] as const) {
      fireEvent.click(screen.getByTestId(`webchat-identity-lang-${lang}`));
      expect(sample()).toContain(marker);
      expect(sample()).toBe(IDENTITY_SAMPLES[lang]);
      expect(sample()).toContain("example.org");
      expect(sample()).toContain("HS256");
      expect(sample()).toContain("exp");
    }
  });
});
