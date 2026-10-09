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
  conflicts: [],
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
  applyVcards.mockReset().mockResolvedValue({
    applied: [{ login: "ibrenner", photo: true, filled: ["phone"], replaced: [] }],
  });
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
      { index: 0, login: "ibrenner", photo: false, fields: true, replace: [] },
    ]);
    expect(await screen.findByTestId("vcard-import-result")).toHaveTextContent("1 Kunde aktualisiert.");
  });

  it("replaces differing fields only when their box is ticked", async () => {
    previewVcards.mockResolvedValue({
      cards: [
        card({
          has_photo: false,
          fill_fields: [],
          conflicts: [{ field: "phone", current: "+49 30 9999", incoming: "+49 30 1234" }],
        }),
      ],
      truncated: false,
    });
    wrap(<VcardImportDialog file={file} onClose={vi.fn()} onCreate={vi.fn()} />);

    expect(await screen.findByTestId("vcard-card-0")).toHaveTextContent("+49 30 9999");
    // Opt-in: nothing selected yet.
    expect(screen.getByTestId("vcard-import-apply")).toBeDisabled();
    fireEvent.click(screen.getByTestId("vcard-card-0-replace"));
    fireEvent.click(screen.getByTestId("vcard-import-apply"));
    await waitFor(() =>
      expect(applyVcards).toHaveBeenCalledWith(file, [
        { index: 0, login: "ibrenner", photo: false, fields: false, replace: ["phone"] },
      ]),
    );
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
    // The preview is asked for this customer, so it lists their blank fields.
    previewVcards.mockResolvedValue({
      cards: [card({ mobile: "+49 170 1", fill_fields: ["phone", "mobile"] })],
      truncated: false,
    });
    const onApplied = vi.fn();
    wrap(<VcardApplyDialog file={file} customer={customer} onClose={vi.fn()} onApplied={onApplied} />);

    const box = await screen.findByTestId("vcard-apply");
    expect(box).toHaveTextContent("+49 30 1234");
    expect(box).toHaveTextContent("+49 170 1");
    expect(box).not.toHaveTextContent("Vorname");

    fireEvent.click(screen.getByTestId("vcard-apply-confirm"));
    await waitFor(() => expect(onApplied).toHaveBeenCalledWith(true));
    expect(previewVcards).toHaveBeenCalledWith(file, "ibrenner");
    expect(applyVcards).toHaveBeenCalledWith(file, [
      { index: 0, login: "ibrenner", photo: true, fields: true, replace: [] },
    ]);
  });

  it("keeps differing fields unless the card's value is picked", async () => {
    previewVcards.mockResolvedValue({
      cards: [
        card({
          has_photo: false,
          fill_fields: [],
          conflicts: [
            { field: "last_name", current: "Brenner", incoming: "Brenner-Wolf" },
            { field: "phone", current: "+49 30 9999", incoming: "+49 30 1234" },
          ],
        }),
      ],
      truncated: false,
    });
    const onApplied = vi.fn();
    wrap(<VcardApplyDialog file={file} customer={customer} onClose={vi.fn()} onApplied={onApplied} />);

    const conflicts = await screen.findByTestId("vcard-conflicts");
    expect(conflicts).toHaveTextContent("2 Felder weichen ab");
    expect(screen.getByTestId("vcard-conflict-phone-keep")).toHaveAttribute("aria-checked", "true");
    // Nothing to do until a card value is picked.
    expect(screen.getByTestId("vcard-apply-confirm")).toBeDisabled();

    fireEvent.click(screen.getByTestId("vcard-conflict-last_name-take"));
    fireEvent.click(screen.getByTestId("vcard-apply-confirm"));
    await waitFor(() => expect(onApplied).toHaveBeenCalledWith(false));
    expect(applyVcards).toHaveBeenCalledWith(file, [
      { index: 0, login: "ibrenner", photo: false, fields: false, replace: ["last_name"] },
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
