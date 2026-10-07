import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { CustomerUserOut } from "@/lib/api";
import type { VCardPreviewCard } from "@/lib/avatarApi";
import { VcardApplyDialog, VcardImportDialog } from "./VcardImport";

const { previewVcards, applyVcards } = vi.hoisted(() => ({
  previewVcards: vi.fn(),
  applyVcards: vi.fn(),
}));

vi.mock("@/lib/avatarApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/avatarApi")>("@/lib/avatarApi");
  return { ...actual, avatarApi: { previewVcards, applyVcards } };
});

const card = (overrides: Partial<VCardPreviewCard>): VCardPreviewCard => ({
  index: 0,
  display_name: "Ilka Brenner",
  first_name: "Ilka",
  last_name: "Brenner",
  emails: ["i.brenner@example.com"],
  phone: "+49 30 1234",
  mobile: null,
  org: "Example GmbH",
  title: null,
  has_photo: true,
  photo_preview: null,
  match_login: "ibrenner",
  match_name: "Ilka Brenner",
  fill_fields: ["phone"],
  has_existing_avatar: false,
  ...overrides,
});

const file = new File(["BEGIN:VCARD"], "kontakte.vcf", { type: "text/vcard" });

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  void i18n.changeLanguage("de");
  previewVcards.mockReset();
  applyVcards.mockReset().mockResolvedValue({ applied: [{ login: "ibrenner", photo: true, filled: ["phone"] }] });
});

describe("VcardImportDialog", () => {
  it("applies the checked parts of matched cards only", async () => {
    previewVcards.mockResolvedValue({
      cards: [card({}), card({ index: 1, display_name: "Neu Kunde", match_login: null, match_name: null, fill_fields: [] })],
      truncated: false,
    });
    wrap(<VcardImportDialog file={file} onClose={vi.fn()} onCreate={vi.fn()} />);

    await screen.findByTestId("vcard-card-0");
    fireEvent.click(screen.getByTestId("vcard-card-0-photo"));
    fireEvent.click(screen.getByTestId("vcard-import-apply"));

    await waitFor(() => expect(applyVcards).toHaveBeenCalledOnce());
    expect(applyVcards).toHaveBeenCalledWith(file, [
      { index: 0, login: "ibrenner", photo: false, fields: true },
    ]);
    expect(await screen.findByTestId("vcard-import-result")).toHaveTextContent("1 Kunde aktualisiert.");
  });

  it("offers unmatched cards to the create form, prefilled", async () => {
    previewVcards.mockResolvedValue({
      cards: [card({ match_login: null, match_name: null, fill_fields: [], mobile: "+49 170 1" })],
      truncated: false,
    });
    const onCreate = vi.fn();
    wrap(<VcardImportDialog file={file} onClose={vi.fn()} onCreate={onCreate} />);

    fireEvent.click(await screen.findByTestId("vcard-card-0-create"));
    expect(onCreate).toHaveBeenCalledWith({
      first_name: "Ilka",
      last_name: "Brenner",
      email: "i.brenner@example.com",
      phone: "+49 30 1234",
      mobile: "+49 170 1",
    });
    // Nothing matched: nothing to apply.
    expect(screen.getByTestId("vcard-import-apply")).toBeDisabled();
  });
});

describe("VcardApplyDialog", () => {
  const customer = {
    login: "ibrenner",
    first_name: "Ilka",
    last_name: "Brenner",
    email: "i.brenner@example.com",
    phone: "",
    mobile: null,
  } as unknown as CustomerUserOut;

  it("fills only the customer's blank fields and takes the photo", async () => {
    previewVcards.mockResolvedValue({ cards: [card({ mobile: "+49 170 1" })], truncated: false });
    const onApplied = vi.fn();
    wrap(<VcardApplyDialog file={file} customer={customer} onClose={vi.fn()} onApplied={onApplied} />);

    const box = await screen.findByTestId("vcard-apply");
    expect(box).toHaveTextContent("+49 30 1234");
    expect(box).toHaveTextContent("+49 170 1");
    expect(box).not.toHaveTextContent("Vorname");

    fireEvent.click(screen.getByTestId("vcard-apply-confirm"));
    await waitFor(() => expect(onApplied).toHaveBeenCalledWith(true));
    expect(applyVcards).toHaveBeenCalledWith(file, [
      { index: 0, login: "ibrenner", photo: true, fields: true },
    ]);
  });

  it("refuses a multi-contact file without a card for this customer", async () => {
    previewVcards.mockResolvedValue({
      cards: [card({ match_login: "other" }), card({ index: 1, match_login: null })],
      truncated: false,
    });
    wrap(<VcardApplyDialog file={file} customer={customer} onClose={vi.fn()} onApplied={vi.fn()} />);

    expect(await screen.findByTestId("vcard-apply-nomatch")).toBeInTheDocument();
    expect(screen.getByTestId("vcard-apply-confirm")).toBeDisabled();
  });
});
