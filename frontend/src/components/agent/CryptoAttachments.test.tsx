import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AttachmentList } from "./ArticleTimeline";
import { formatFingerprint } from "@/lib/cryptoAttachment";

const {
  listAttachments,
  attachmentDownloadUrl,
  getAttachmentPgpKey,
  importAttachmentPgpKey,
  getAttachmentHtml,
} = vi.hoisted(() => ({
  listAttachments: vi.fn(),
  attachmentDownloadUrl: vi.fn(
    (t: number, a: number, id: number, download?: boolean) =>
      `/att/${t}/${a}/${id}${download ? "?download=true" : ""}`,
  ),
  getAttachmentPgpKey: vi.fn(),
  importAttachmentPgpKey: vi.fn(),
  getAttachmentHtml: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      listAttachments,
      attachmentDownloadUrl,
      getAttachmentPgpKey,
      importAttachmentPgpKey,
      getAttachmentHtml,
    },
  };
});

const FP = "BADF32B47B9287F01513E6FE350F4F42BC412BEF";

const KEY = {
  fingerprint: FP,
  key_id: FP.slice(-16),
  uids: ["Erika Beispiel <erika@example.org>"],
  emails: ["erika@example.org"],
  algorithm: "RSA",
  bits: 4096,
  created: "2024-01-15T10:00:00Z",
  expires: null,
  status: "good",
  in_keyring: false,
};

const ATTS = [
  { id: 1, filename: "angebot.pdf", content_type: "application/pdf", content_size: "20480" },
  {
    id: 2,
    filename: "PGPexch.htm",
    content_type: "text/html",
    content_size: "65126",
    crypto_kind: "pgp_html_body",
  },
  {
    id: 3,
    filename: "public_key_erika@example.org.asc",
    content_type: "application/pgp-keys",
    content_size: "7884",
    crypto_kind: "pgp_public_key",
  },
  {
    id: 4,
    filename: "signature.asc",
    content_type: "application/pgp-signature",
    content_size: "833",
    crypto_kind: "pgp_signature",
  },
];

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AttachmentList ticketId={7} articleId={3} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("crypto attachments", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("en");
  });

  beforeEach(() => {
    for (const fn of [
      listAttachments,
      getAttachmentPgpKey,
      importAttachmentPgpKey,
      getAttachmentHtml,
    ]) {
      fn.mockReset();
    }
  });

  it("groups side files below the real attachments, key card first", async () => {
    listAttachments.mockResolvedValue(ATTS);
    getAttachmentPgpKey.mockResolvedValue({ available: true, keys: [KEY], can_import: false });
    wrap();

    const group = await screen.findByTestId("attachment-crypto-group");
    expect(within(group).getByText("Signature & keys")).toBeTruthy();
    // The ordinary attachment stays in the normal list, not in the group.
    expect(within(group).queryByText("angebot.pdf")).toBeNull();
    expect(screen.getByText("angebot.pdf")).toBeTruthy();

    const rows = within(group).getAllByTestId(/^attachment-crypto-\d+$/);
    expect(rows.map((r) => r.getAttribute("data-kind"))).toEqual([
      "pgp_public_key",
      "pgp_html_body",
      "pgp_signature",
    ]);
    expect(within(rows[1]).getByText("HTML version of the message")).toBeTruthy();
    expect(within(rows[2]).getByText("PGP signature")).toBeTruthy();
    // Still downloadable under its real name.
    expect(screen.getByTestId("attachment-4").getAttribute("href")).toBe("/att/7/3/4?download=true");
  });

  it("shows the parsed key: uid, grouped fingerprint, keyring state", async () => {
    listAttachments.mockResolvedValue([ATTS[2]]);
    getAttachmentPgpKey.mockResolvedValue({ available: true, keys: [KEY], can_import: false });
    wrap();

    expect(await screen.findByText("Erika Beispiel <erika@example.org>")).toBeTruthy();
    expect(screen.getByTestId("pgp-key-fingerprint").textContent).toBe(formatFingerprint(FP));
    expect(screen.getByText("RSA 4096")).toBeTruthy();
    expect(screen.getByTestId("pgp-key-not-in-keyring")).toBeTruthy();
    // No import right → no button.
    expect(screen.queryByTestId("pgp-key-import-3")).toBeNull();
    expect(getAttachmentPgpKey).toHaveBeenCalledWith(7, 3, 3, expect.anything());
  });

  it("imports the key when allowed and flips to 'in keyring'", async () => {
    listAttachments.mockResolvedValue([ATTS[2]]);
    getAttachmentPgpKey.mockResolvedValue({ available: true, keys: [KEY], can_import: true });
    importAttachmentPgpKey.mockResolvedValue({
      available: true,
      keys: [{ ...KEY, in_keyring: true }],
      can_import: true,
    });
    wrap();

    fireEvent.click(await screen.findByTestId("pgp-key-import-3"));
    await screen.findByTestId("pgp-key-in-keyring");
    expect(importAttachmentPgpKey).toHaveBeenCalledWith(7, 3, 3);
    expect(screen.queryByTestId("pgp-key-import-3")).toBeNull();
  });

  it("falls back to the plain explanation when gpg cannot read the key", async () => {
    listAttachments.mockResolvedValue([ATTS[2]]);
    getAttachmentPgpKey.mockResolvedValue({ available: false, keys: [], can_import: false });
    wrap();

    expect(await screen.findByTestId("pgp-key-unavailable")).toBeTruthy();
    expect(screen.getByText("PGP key")).toBeTruthy();
  });

  it("opens PGPexch.htm as a sanitised preview", async () => {
    listAttachments.mockResolvedValue([ATTS[1]]);
    getAttachmentHtml.mockResolvedValue({
      article_id: 3,
      content_type: "text/html",
      is_html: true,
      body: "<p>Hallo</p>",
    });
    wrap();

    fireEvent.click(await screen.findByTestId("attachment-show-2"));
    await waitFor(() => expect(getAttachmentHtml).toHaveBeenCalledWith(7, 3, 2, expect.anything()));
    expect(await screen.findByTestId("attachment-html-2")).toBeTruthy();
  });

  it("formats fingerprints like gpg", () => {
    expect(formatFingerprint(FP)).toBe(
      "BADF 32B4 7B92 87F0 1513  E6FE 350F 4F42 BC41 2BEF",
    );
    expect(formatFingerprint("abcd1234")).toBe("ABCD 1234");
  });
});
