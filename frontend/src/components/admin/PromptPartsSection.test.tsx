import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { PromptPartsSection } from "./PromptPartsSection";

const {
  listPromptParts,
  createPromptPart,
  updatePromptPart,
  deletePromptPart,
  reorderPromptParts,
} = vi.hoisted(() => ({
  listPromptParts: vi.fn(),
  createPromptPart: vi.fn(),
  updatePromptPart: vi.fn(),
  deletePromptPart: vi.fn(),
  reorderPromptParts: vi.fn(),
}));

vi.mock("@/lib/aiApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/aiApi")>("@/lib/aiApi");
  return {
    ...actual,
    aiApi: {
      ...actual.aiApi,
      listPromptParts,
      createPromptPart,
      updatePromptPart,
      deletePromptPart,
      reorderPromptParts,
    },
  };
});

function wrap() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PromptPartsSection policyId={5} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

function part(id: number, extra: Partial<Record<string, unknown>> = {}) {
  return {
    id,
    policy_id: 5,
    kind: "note",
    title: `Part ${id}`,
    content: `content ${id}`,
    position: id,
    enabled: true,
    create_time: "2026-07-23T10:00:00",
    change_time: "2026-07-23T10:00:00",
    ...extra,
  };
}

describe("PromptPartsSection", () => {
  beforeEach(() => {
    listPromptParts.mockReset().mockResolvedValue([]);
    createPromptPart.mockReset().mockResolvedValue(part(99));
    updatePromptPart.mockReset().mockResolvedValue(part(1));
    deletePromptPart.mockReset().mockResolvedValue(undefined);
    reorderPromptParts.mockReset().mockResolvedValue([]);
  });

  it("renders the empty state when there are no parts", async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId("admin-ai-prompt-parts-empty")).toBeTruthy());
  });

  it("shows parts with title, char count and disabled styling", async () => {
    listPromptParts.mockResolvedValue([
      part(1, { kind: "file", title: "kb.md", content: "abcde" }),
      part(2, { enabled: false }),
    ]);
    wrap();
    await waitFor(() => expect(screen.getByTestId("admin-ai-prompt-part-1")).toBeTruthy());
    expect(screen.getByTestId("admin-ai-prompt-part-1").textContent).toContain("kb.md");
    expect(screen.getByTestId("admin-ai-prompt-part-2").className).toContain("opacity-60");
  });

  it("creates a note via the inline form", async () => {
    wrap();
    await waitFor(() => expect(listPromptParts).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("admin-ai-prompt-parts-add-note"));
    fireEvent.change(screen.getByTestId("admin-ai-prompt-parts-note-title"), {
      target: { value: "Du-Form" },
    });
    fireEvent.change(screen.getByTestId("admin-ai-prompt-parts-note-text"), {
      target: { value: "Antworte immer mit Du-Form." },
    });
    fireEvent.click(screen.getByTestId("admin-ai-prompt-parts-note-save"));

    await waitFor(() =>
      expect(createPromptPart).toHaveBeenCalledWith(5, {
        kind: "note",
        title: "Du-Form",
        content: "Antworte immer mit Du-Form.",
      }),
    );
  });

  it("uploads multiple markdown files as individual parts", async () => {
    wrap();
    await waitFor(() => expect(listPromptParts).toHaveBeenCalled());

    const input = screen.getByTestId("admin-ai-prompt-parts-file-input");
    const fileA = new File(["# A"], "a.md", { type: "text/markdown" });
    const fileB = new File(["# B"], "b.md", { type: "text/markdown" });
    fireEvent.change(input, { target: { files: [fileA, fileB] } });

    await waitFor(() => expect(createPromptPart).toHaveBeenCalledTimes(2));
    expect(createPromptPart).toHaveBeenCalledWith(5, {
      kind: "file",
      title: "a.md",
      content: "# A",
    });
    expect(createPromptPart).toHaveBeenCalledWith(5, {
      kind: "file",
      title: "b.md",
      content: "# B",
    });
  });

  it("toggles enabled, reorders, and deletes via the API", async () => {
    listPromptParts.mockResolvedValue([part(1), part(2)]);
    wrap();
    await waitFor(() => expect(screen.getByTestId("admin-ai-prompt-part-1")).toBeTruthy());

    fireEvent.click(screen.getByTestId("admin-ai-prompt-part-enabled-1"));
    await waitFor(() =>
      expect(updatePromptPart).toHaveBeenCalledWith(5, 1, { enabled: false }),
    );

    fireEvent.click(screen.getByTestId("admin-ai-prompt-part-down-1"));
    await waitFor(() => expect(reorderPromptParts).toHaveBeenCalledWith(5, [2, 1]));

    fireEvent.click(screen.getByTestId("admin-ai-prompt-part-delete-2"));
    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(deletePromptPart).toHaveBeenCalledWith(5, 2));
  });

  it("edits a note's content inline", async () => {
    listPromptParts.mockResolvedValue([part(1)]);
    wrap();
    await waitFor(() => expect(screen.getByTestId("admin-ai-prompt-part-1")).toBeTruthy());

    fireEvent.click(screen.getByTestId("admin-ai-prompt-part-toggle-1"));
    expect(screen.getByTestId("admin-ai-prompt-part-content-1").textContent).toBe("content 1");

    fireEvent.click(screen.getByTestId("admin-ai-prompt-part-edit-button-1"));
    fireEvent.change(screen.getByTestId("admin-ai-prompt-part-edit-1"), {
      target: { value: "updated" },
    });
    fireEvent.click(screen.getByTestId("admin-ai-prompt-part-edit-save-1"));

    await waitFor(() =>
      expect(updatePromptPart).toHaveBeenCalledWith(5, 1, { content: "updated" }),
    );
  });

  it("rejects oversized files without calling the API", async () => {
    wrap();
    await waitFor(() => expect(listPromptParts).toHaveBeenCalled());

    const big = new File([new Uint8Array(256 * 1024 + 1)], "big.md", {
      type: "text/markdown",
    });
    fireEvent.change(screen.getByTestId("admin-ai-prompt-parts-file-input"), {
      target: { files: [big] },
    });

    await waitFor(() =>
      expect(screen.getByTestId("admin-ai-prompt-parts-file-error")).toBeTruthy(),
    );
    expect(createPromptPart).not.toHaveBeenCalled();
  });
});
