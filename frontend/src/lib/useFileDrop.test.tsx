import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { isAvatarImage, isVcardFile, useFileDrop, type DropRejection } from "@/lib/useFileDrop";

function Zone({
  onFiles,
  onReject,
  disabled,
  vcardText,
}: {
  onFiles: (files: File[]) => void;
  onReject?: (rejection: DropRejection) => void;
  disabled?: boolean;
  vcardText?: boolean;
}) {
  const { active, dropProps } = useFileDrop({
    accept: isVcardFile,
    onFiles,
    onReject,
    disabled,
    vcardText,
  });
  return (
    <div data-testid="zone" data-active={active} {...dropProps}>
      <span data-testid="child">child</span>
    </div>
  );
}

const files = (...list: File[]) => ({ dataTransfer: { types: ["Files"], files: list, dropEffect: "none" } });
const vcf = new File(["BEGIN:VCARD"], "kontakte.vcf", { type: "" });
const png = new File(["x"], "a.png", { type: "image/png" });

describe("useFileDrop", () => {
  it("keeps the overlay while the pointer crosses children and clears it on leave", () => {
    render(<Zone onFiles={vi.fn()} />);
    const zone = screen.getByTestId("zone");
    fireEvent.dragEnter(zone, files());
    fireEvent.dragEnter(screen.getByTestId("child"), files());
    fireEvent.dragLeave(screen.getByTestId("child"), files());
    expect(zone).toHaveAttribute("data-active", "true");
    fireEvent.dragLeave(zone, files());
    expect(zone).toHaveAttribute("data-active", "false");
  });

  it("hands over accepted files on drop and resets the overlay", () => {
    const onFiles = vi.fn();
    render(<Zone onFiles={onFiles} />);
    const zone = screen.getByTestId("zone");
    fireEvent.dragEnter(zone, files());
    fireEvent.drop(zone, files(vcf, png));
    expect(onFiles).toHaveBeenCalledWith([vcf]);
    expect(zone).toHaveAttribute("data-active", "false");
  });

  it("rejects a drop without any accepted file", () => {
    const onFiles = vi.fn();
    const onReject = vi.fn();
    render(<Zone onFiles={onFiles} onReject={onReject} />);
    fireEvent.drop(screen.getByTestId("zone"), files(png));
    expect(onFiles).not.toHaveBeenCalled();
    expect(onReject).toHaveBeenCalledWith({ files: [png], types: ["Files"] });
  });

  it("ignores drags of non-files (text, links)", () => {
    render(<Zone onFiles={vi.fn()} />);
    const zone = screen.getByTestId("zone");
    fireEvent.dragEnter(zone, { dataTransfer: { types: ["text/plain"], files: [] } });
    expect(zone).toHaveAttribute("data-active", "false");
  });

  it("takes a contact that arrives as text (Apple Contacts without a file)", () => {
    const onFiles = vi.fn();
    render(<Zone onFiles={onFiles} vcardText />);
    const zone = screen.getByTestId("zone");
    const card = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ilka Brenner\r\nEND:VCARD\r\n";
    const dataTransfer = {
      types: ["text/plain"],
      files: [],
      getData: (type: string) => (type === "text/plain" ? card : ""),
    };
    fireEvent.dragEnter(zone, { dataTransfer });
    expect(zone).toHaveAttribute("data-active", "true");
    fireEvent.drop(zone, { dataTransfer });
    expect(onFiles).toHaveBeenCalledOnce();
    const [[file]] = onFiles.mock.calls[0] as [[File]];
    expect(file.name).toBe("kontakt.vcf");
    expect(file.type).toBe("text/vcard");
    // jsdom's File has no text(); the card is ASCII, so bytes = characters.
    expect(file.size).toBe(card.length);
  });

  it("reports what a text drag without a contact carried", () => {
    const onReject = vi.fn();
    render(<Zone onFiles={vi.fn()} onReject={onReject} vcardText />);
    fireEvent.drop(screen.getByTestId("zone"), {
      dataTransfer: { types: ["text/plain"], files: [], getData: () => "Ilka Brenner" },
    });
    expect(onReject).toHaveBeenCalledWith({ files: [], types: ["text/plain"] });
  });

  it("ignores drags that started on the page itself", () => {
    render(<Zone onFiles={vi.fn()} vcardText />);
    const zone = screen.getByTestId("zone");
    fireEvent.dragStart(document.body);
    fireEvent.dragEnter(zone, files());
    expect(zone).toHaveAttribute("data-active", "false");
    fireEvent.dragEnd(document.body);
    fireEvent.dragEnter(zone, files());
    expect(zone).toHaveAttribute("data-active", "true");
  });

  it("does nothing while disabled", () => {
    const onFiles = vi.fn();
    render(<Zone onFiles={onFiles} disabled />);
    const zone = screen.getByTestId("zone");
    fireEvent.dragEnter(zone, files());
    fireEvent.drop(zone, files(vcf));
    expect(zone).toHaveAttribute("data-active", "false");
    expect(onFiles).not.toHaveBeenCalled();
  });
});

function Nested({ outer, inner }: { outer: (f: File[]) => void; inner: (f: File[]) => void }) {
  const o = useFileDrop({ accept: isVcardFile, onFiles: outer });
  const i = useFileDrop({ accept: isVcardFile, onFiles: inner });
  return (
    <div data-testid="outer" data-active={o.active} {...o.dropProps}>
      <span data-testid="outside">x</span>
      <div data-testid="inner" data-active={i.active} {...i.dropProps} />
    </div>
  );
}

describe("nested drop zones", () => {
  it("lets the inner zone take the drop alone and hides the outer overlay meanwhile", () => {
    const outer = vi.fn();
    const inner = vi.fn();
    render(<Nested outer={outer} inner={inner} />);
    fireEvent.dragEnter(screen.getByTestId("outside"), files());
    expect(screen.getByTestId("outer")).toHaveAttribute("data-active", "true");
    // Moving into the inner zone: enter(inner) fires before leave(outside).
    fireEvent.dragEnter(screen.getByTestId("inner"), files());
    fireEvent.dragLeave(screen.getByTestId("outside"), files());
    expect(screen.getByTestId("outer")).toHaveAttribute("data-active", "false");
    expect(screen.getByTestId("inner")).toHaveAttribute("data-active", "true");

    fireEvent.drop(screen.getByTestId("inner"), files(vcf));
    expect(inner).toHaveBeenCalledWith([vcf]);
    expect(outer).not.toHaveBeenCalled();
  });
});

describe("file type checks", () => {
  it("recognises vCards by extension or MIME type", () => {
    expect(isVcardFile(vcf)).toBe(true);
    expect(isVcardFile(new File([""], "x", { type: "text/x-vcard" }))).toBe(true);
    expect(isVcardFile(png)).toBe(false);
  });

  it("accepts only PNG, JPEG and WebP as avatar images", () => {
    expect(isAvatarImage(png)).toBe(true);
    expect(isAvatarImage(new File([""], "a.webp", { type: "image/webp" }))).toBe(true);
    expect(isAvatarImage(new File([""], "a.gif", { type: "image/gif" }))).toBe(false);
  });
});
