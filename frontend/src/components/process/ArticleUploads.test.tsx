import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { useChatAttachments } from "@/components/agent/telegram/useChatAttachments";
import { ArticleUploads } from "./ArticleUploads";

let latest: ReturnType<typeof useChatAttachments> | null = null;
function Harness() {
  const uploads = useChatAttachments();
  latest = uploads;
  return <ArticleUploads uploads={uploads} />;
}

function wrap() {
  return render(
    <I18nextProvider i18n={i18n}>
      <Harness />
    </I18nextProvider>,
  );
}

const pick = (files: File[]) => fireEvent.change(screen.getByTestId("process-uploads-input"), { target: { files } });

describe("ArticleUploads", () => {
  it("uploads chips: add, remove, size limit message", async () => {
    wrap();
    pick([new File(["hallo"], "wf3-notiz.txt", { type: "text/plain" })]);
    expect(await screen.findByText("wf3-notiz.txt")).toBeInTheDocument();
    await waitFor(() => expect(latest?.payload).toEqual([
      { filename: "wf3-notiz.txt", content_type: "text/plain", content_base64: btoa("hallo") },
    ]));

    fireEvent.click(screen.getByTestId("tg-composer-attachment-remove-0"));
    expect(screen.queryByText("wf3-notiz.txt")).toBeNull();
    expect(latest?.payload).toEqual([]);

    // a batch over the total size limit is refused as a whole, with the reply dialog's message
    const big = new File(["x"], "wf3-gross.bin");
    Object.defineProperty(big, "size", { value: 19 * 1024 * 1024 });
    pick([big]);
    expect(await screen.findByTestId("process-uploads-error")).toHaveTextContent("18.0 MB");
    expect(screen.queryByText("wf3-gross.bin")).toBeNull();
  });

  it("refuses more than ten files", async () => {
    wrap();
    pick(Array.from({ length: 11 }, (_, i) => new File(["x"], `wf3-${i}.txt`)));
    expect(await screen.findByTestId("process-uploads-error")).toHaveTextContent("10");
    expect(latest?.items).toHaveLength(0);
  });
});
