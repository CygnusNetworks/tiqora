import { describe, it, expect, vi, beforeEach } from "vitest";
import { useState } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import type { PickedMention } from "@/lib/mentions";
import { MentionTextarea } from "./MentionTextarea";

const { listReferenceAgents } = vi.hoisted(() => ({ listReferenceAgents: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { listReferenceAgents } };
});

/** Drives the component the way a composer does, so insertions are visible. */
function Harness({ onMentions }: { onMentions?: (m: PickedMention[]) => void }) {
  const [value, setValue] = useState("");
  const [mentions, setMentions] = useState<PickedMention[]>([]);
  return (
    <MentionTextarea
      value={value}
      onChange={setValue}
      mentions={mentions}
      onMentionsChange={(next) => {
        setMentions(next);
        onMentions?.(next);
      }}
      testId="body"
    />
  );
}

function wrap(onMentions?: (m: PickedMention[]) => void) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <Harness onMentions={onMentions} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

/** `fireEvent.change` leaves the caret at 0; the typeahead reads it. */
function type(el: HTMLTextAreaElement, value: string) {
  fireEvent.change(el, { target: { value } });
  el.setSelectionRange(value.length, value.length);
  fireEvent.keyUp(el, { key: "a" });
}

describe("MentionTextarea", () => {
  beforeEach(() => {
    listReferenceAgents.mockReset().mockResolvedValue([
      { id: 2, login: "ada", full_name: "Ada Lovelace" },
      { id: 3, login: "bob", full_name: "Bob Stone" },
    ]);
  });

  it("stays out of the way until an @ is typed", () => {
    wrap();
    type(screen.getByTestId("body") as HTMLTextAreaElement, "kein Vorschlag hier");
    expect(screen.queryByTestId("mention-typeahead")).not.toBeInTheDocument();
    expect(listReferenceAgents).not.toHaveBeenCalled();
  });

  it("suggests agents once an @ is typed", async () => {
    wrap();
    type(screen.getByTestId("body") as HTMLTextAreaElement, "bitte @");
    expect(await screen.findByTestId("mention-typeahead")).toBeInTheDocument();
    expect(screen.getByTestId("mention-option-2")).toHaveTextContent("Ada Lovelace");
    expect(screen.getByTestId("mention-option-3")).toHaveTextContent("Bob Stone");
  });

  it("narrows the list by name and by login", async () => {
    wrap();
    const el = screen.getByTestId("body") as HTMLTextAreaElement;
    type(el, "@lovel");
    await screen.findByTestId("mention-option-2");
    expect(screen.queryByTestId("mention-option-3")).not.toBeInTheDocument();
    type(el, "@bob");
    await screen.findByTestId("mention-option-3");
    expect(screen.queryByTestId("mention-option-2")).not.toBeInTheDocument();
  });

  it("does not open on an email address", async () => {
    wrap();
    type(screen.getByTestId("body") as HTMLTextAreaElement, "schreib an bob@example.com");
    await waitFor(() => expect(screen.queryByTestId("mention-typeahead")).not.toBeInTheDocument());
  });

  it("inserts the picked name and records the mention", async () => {
    const onMentions = vi.fn();
    wrap(onMentions);
    const el = screen.getByTestId("body") as HTMLTextAreaElement;
    type(el, "bitte @ad");
    fireEvent.mouseDown(await screen.findByTestId("mention-option-2"));
    await waitFor(() => expect(el).toHaveValue("bitte @Ada Lovelace "));
    expect(onMentions).toHaveBeenCalledWith([{ id: 2, name: "Ada Lovelace" }]);
    expect(screen.queryByTestId("mention-typeahead")).not.toBeInTheDocument();
  });

  it("picks the highlighted entry with Enter after arrowing down", async () => {
    const onMentions = vi.fn();
    wrap(onMentions);
    const el = screen.getByTestId("body") as HTMLTextAreaElement;
    type(el, "@");
    await screen.findByTestId("mention-typeahead");
    fireEvent.keyDown(el, { key: "ArrowDown" });
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(el).toHaveValue("@Bob Stone "));
    expect(onMentions).toHaveBeenCalledWith([{ id: 3, name: "Bob Stone" }]);
  });

  it("closes on Escape without inserting", async () => {
    wrap();
    const el = screen.getByTestId("body") as HTMLTextAreaElement;
    type(el, "@ad");
    await screen.findByTestId("mention-typeahead");
    fireEvent.keyDown(el, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("mention-typeahead")).not.toBeInTheDocument());
    expect(el).toHaveValue("@ad");
  });
});
