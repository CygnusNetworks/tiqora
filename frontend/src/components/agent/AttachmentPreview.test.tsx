import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AttachmentList } from "./ArticleTimeline";

const { listAttachments, attachmentDownloadUrl } = vi.hoisted(() => ({
  listAttachments: vi.fn(),
  attachmentDownloadUrl: vi.fn(
    (t: number, a: number, id: number, download?: boolean) =>
      `/att/${t}/${a}/${id}${download ? "?download=true" : ""}`,
  ),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { listAttachments, attachmentDownloadUrl } };
});

type Att = {
  id: number;
  filename: string;
  content_type: string;
  content_size: string;
  inline?: boolean;
};

const IMG = (id: number, filename: string, size = "204800"): Att => ({
  id,
  filename,
  content_type: "image/jpeg",
  content_size: size,
  inline: false,
});

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

describe("AttachmentList image previews", () => {
  beforeEach(() => {
    listAttachments.mockReset();
    attachmentDownloadUrl.mockClear();
  });

  it("shows images as thumbnails and leaves other files as rows", async () => {
    listAttachments.mockResolvedValue([
      IMG(1, "verteiler.jpg"),
      { id: 2, filename: "logs.zip", content_type: "application/zip", content_size: "1024" },
    ]);
    wrap();

    const thumb = await screen.findByTestId("attachment-thumb-1");
    // Thumbnails render inline (no download disposition) so the browser paints them.
    expect(thumb.querySelector("img")?.getAttribute("src")).toBe("/att/7/3/1");
    expect(screen.getByTestId("attachment-2")).toBeTruthy();
    expect(screen.queryByTestId("attachment-thumb-2")).toBeNull();
  });

  it("keeps oversized images as plain rows", async () => {
    listAttachments.mockResolvedValue([IMG(1, "raw.jpg", String(20 * 1024 * 1024))]);
    wrap();

    await screen.findByTestId("attachment-1");
    expect(screen.queryByTestId("attachment-strip")).toBeNull();
  });

  it("does not preview embedded cid: parts", async () => {
    listAttachments.mockResolvedValue([{ ...IMG(1, "logo.png"), inline: true }]);
    wrap();

    await screen.findByTestId("attachment-inline-group");
    expect(screen.queryByTestId("attachment-strip")).toBeNull();
  });

  it("falls back to a file row when the image fails to load", async () => {
    listAttachments.mockResolvedValue([IMG(1, "kaputt.jpg")]);
    wrap();

    const img = (await screen.findByTestId("attachment-thumb-1")).querySelector("img")!;
    fireEvent.error(img);
    await waitFor(() => expect(screen.queryByTestId("attachment-strip")).toBeNull());
    expect(screen.getByTestId("attachment-1")).toBeTruthy();
  });

  it("collapses a long strip behind a +N tile", async () => {
    listAttachments.mockResolvedValue(
      Array.from({ length: 8 }, (_, i) => IMG(i + 1, `foto-${i + 1}.jpg`)),
    );
    wrap();

    await screen.findByTestId("attachment-strip");
    expect(screen.getAllByTestId(/^attachment-thumb-/)).toHaveLength(6);
    expect(screen.getByTestId("attachment-strip-more").textContent).toBe("+2");
  });

  it("opens the lightbox on the clicked image and steps with the arrow keys", async () => {
    listAttachments.mockResolvedValue([IMG(1, "eins.jpg"), IMG(2, "zwei.jpg"), IMG(3, "drei.jpg")]);
    wrap();

    fireEvent.click(await screen.findByTestId("attachment-thumb-2"));
    const lb = screen.getByTestId("attachment-lightbox");
    expect(screen.getByTestId("lightbox-name").textContent).toBe("zwei.jpg");
    expect(screen.getByTestId("lightbox-count").textContent).toBe("2 of 3");

    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByTestId("lightbox-name").textContent).toBe("drei.jpg");
    // Wraps around rather than dead-ending at the last image.
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByTestId("lightbox-name").textContent).toBe("eins.jpg");

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(lb.isConnected).toBe(false));
  });

  it("offers the download disposition inside the lightbox", async () => {
    listAttachments.mockResolvedValue([IMG(1, "eins.jpg")]);
    wrap();

    fireEvent.click(await screen.findByTestId("attachment-thumb-1"));
    const link = screen.getByRole("link", { name: /download/i });
    expect(link.getAttribute("href")).toBe("/att/7/3/1?download=true");
    // Single image: no stepping affordances to distract.
    expect(screen.queryByTestId("lightbox-count")?.textContent).toBe("1 of 1");
  });
});
