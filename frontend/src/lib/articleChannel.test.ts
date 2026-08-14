import { describe, it, expect } from "vitest";
import type { ArticleListItem } from "@/lib/api";
import {
  channelIcon,
  channelNameOf,
  dominantChannel,
  emailFromAddress,
  formatFromAddress,
  formatToAddresses,
  initialsFor,
  isConversationalChannel,
  isInternalNote,
  senderDisplayName,
} from "./articleChannel";

describe("emailFromAddress", () => {
  it("extracts the address from a \"Name <mail@host>\" from_address", () => {
    expect(emailFromAddress("Ada Lovelace <ada@example.com>")).toBe("ada@example.com");
  });

  it("passes through a bare address", () => {
    expect(emailFromAddress("ada@example.com")).toBe("ada@example.com");
  });

  it("returns undefined for missing or unparseable input", () => {
    expect(emailFromAddress(null)).toBeUndefined();
    expect(emailFromAddress(undefined)).toBeUndefined();
    expect(emailFromAddress("not an address")).toBeUndefined();
  });
});

describe("senderDisplayName", () => {
  it("strips quotes from a quoted \"Last, First\" display name", () => {
    expect(senderDisplayName('"Luhmer, Bastian" <l@x.de>')).toBe("Luhmer, Bastian");
  });

  it("strips quotes from a single-quoted display name", () => {
    expect(senderDisplayName("'Netadmin StudNet Bonn' <n@y.de>")).toBe("Netadmin StudNet Bonn");
  });

  it("falls back to the bare address when there is no display name", () => {
    expect(senderDisplayName("mail@host.de")).toBe("mail@host.de");
  });

  it("returns null for missing input", () => {
    expect(senderDisplayName(null)).toBeNull();
    expect(senderDisplayName(undefined)).toBeNull();
    expect(senderDisplayName("")).toBeNull();
  });
});

describe("initialsFor", () => {
  const article = (from_address: string | null): ArticleListItem =>
    ({ from_address }) as ArticleListItem;

  it("takes the first letters of a quoted \"Last, First\" name", () => {
    expect(initialsFor(article('"Luhmer, Bastian" <l@x.de>'))).toBe("LB");
  });

  it("takes the first letters of a quoted multi-word name", () => {
    expect(initialsFor(article("'Netadmin StudNet Bonn' <n@y.de>"))).toBe("NS");
  });

  it("falls back to the local-part of a bare address", () => {
    expect(initialsFor(article("mail@host.de"))).toBe("MA");
  });

  it("falls back to a placeholder for missing input", () => {
    expect(initialsFor(article(null))).toBe("?");
  });
});

function article(overrides: Partial<ArticleListItem>): ArticleListItem {
  return {
    id: 1,
    communication_channel_id: 1,
    communication_channel_name: null,
    sender_type: "customer",
    is_visible_for_customer: true,
    ...overrides,
  } as ArticleListItem;
}

describe("channelNameOf", () => {
  it("uses communication_channel_name when the backend sent one", () => {
    expect(
      channelNameOf(article({ communication_channel_id: 999, communication_channel_name: "Telegram" })),
    ).toBe("Telegram");
  });

  it("falls back to the legacy numeric-id mapping when name is null", () => {
    expect(channelNameOf(article({ communication_channel_id: 1, communication_channel_name: null }))).toBe(
      "Email",
    );
    expect(channelNameOf(article({ communication_channel_id: 2, communication_channel_name: null }))).toBe(
      "Phone",
    );
    expect(channelNameOf(article({ communication_channel_id: 3, communication_channel_name: null }))).toBe(
      "Internal",
    );
    expect(channelNameOf(article({ communication_channel_id: 4, communication_channel_name: null }))).toBe(
      "Chat",
    );
  });

  it("defaults to Email for an unrecognized id and no name (old app behavior)", () => {
    expect(channelNameOf(article({ communication_channel_id: 42, communication_channel_name: null }))).toBe(
      "Email",
    );
  });
});

describe("isConversationalChannel / channelIcon", () => {
  it("treats Telegram as conversational, with the paper-plane icon", () => {
    expect(isConversationalChannel("Telegram")).toBe(true);
    expect(channelIcon("Telegram")).toBe("✈");
  });

  it("treats Email as non-conversational, with the envelope icon", () => {
    expect(isConversationalChannel("Email")).toBe(false);
    expect(channelIcon("Email")).toBe("✉");
  });

  it("keeps the legacy Chat channel conversational with the speech-bubble icon", () => {
    expect(isConversationalChannel("Chat")).toBe(true);
    expect(channelIcon("Chat")).toBe("💬");
  });
});

describe("isInternalNote", () => {
  it("is true for a non-customer-visible article on the Internal channel (by name)", () => {
    expect(
      isInternalNote(
        article({ communication_channel_id: 999, communication_channel_name: "Internal", is_visible_for_customer: false }),
      ),
    ).toBe(true);
  });

  it("still matches legacy id-3 data with no name (unchanged old behavior)", () => {
    expect(
      isInternalNote(
        article({ communication_channel_id: 3, communication_channel_name: null, is_visible_for_customer: false }),
      ),
    ).toBe(true);
  });

  it("is false when visible for the customer even on the Internal channel", () => {
    expect(
      isInternalNote(
        article({ communication_channel_id: 3, communication_channel_name: null, is_visible_for_customer: true }),
      ),
    ).toBe(false);
  });
});

describe("dominantChannel", () => {
  it("returns the most common channel name among customer articles", () => {
    const articles = [
      article({ id: 1, sender_type: "customer", communication_channel_name: "Telegram" }),
      article({ id: 2, sender_type: "customer", communication_channel_name: "Telegram" }),
      article({ id: 3, sender_type: "agent", communication_channel_name: "Email" }),
    ];
    expect(dominantChannel(articles)).toBe("Telegram");
  });

  it("falls back to all articles when there are no customer articles", () => {
    const articles = [
      article({ id: 1, sender_type: "agent", communication_channel_name: "Internal" }),
      article({ id: 2, sender_type: "agent", communication_channel_name: "Internal" }),
      article({ id: 3, sender_type: "system", communication_channel_name: "Email" }),
    ];
    expect(dominantChannel(articles)).toBe("Internal");
  });

  it("returns null for an empty list", () => {
    expect(dominantChannel([])).toBeNull();
  });
});

describe("formatFromAddress / formatToAddresses", () => {
  it("re-renders a quoted \"Name <email>\" header without the quotes", () => {
    expect(formatFromAddress('"Luhmer, Bastian" <l@x.de>')).toBe("Luhmer, Bastian <l@x.de>");
  });

  it("passes through a bare address unchanged", () => {
    expect(formatFromAddress("mail@host.de")).toBe("mail@host.de");
  });

  it("re-renders each recipient in a comma-joined To header without quotes", () => {
    expect(
      formatToAddresses('"Luhmer, Bastian" <l@x.de>, \'Netadmin StudNet Bonn\' <n@y.de>'),
    ).toBe("Luhmer, Bastian <l@x.de>, Netadmin StudNet Bonn <n@y.de>");
  });
});
