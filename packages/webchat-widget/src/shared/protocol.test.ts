import { describe, expect, it } from "vitest";
import { PROTOCOL, isFrameToHost, isHostToFrame, isPreviewConfig } from "./protocol";

const theme = { primaryColor: "#2563eb", position: "right", mode: "auto" };
const ready = {
  p: PROTOCOL,
  type: "ready",
  online: true,
  hidden: false,
  launcherLabel: "x",
  theme,
  dir: "ltr",
};

describe("protocol guards", () => {
  it("accepts only well-formed frame messages", () => {
    expect(isFrameToHost({ p: PROTOCOL, type: "availability", online: true })).toBe(true);
    expect(isFrameToHost({ p: PROTOCOL, type: "availability", online: "yes" })).toBe(false);
    expect(isFrameToHost({ p: "other", type: "availability", online: true })).toBe(false);
    expect(isFrameToHost(ready)).toBe(true);
    expect(
      isFrameToHost({ ...ready, theme: { ...theme, primaryColor: "red;background:url(x)" } }),
    ).toBe(false);
    expect(isFrameToHost("string")).toBe(false);
  });

  it("rejects extra keys, unknown types and bad values", () => {
    expect(isFrameToHost({ ...ready, body: "secret message" })).toBe(false);
    expect(isFrameToHost({ ...ready, dir: "auto" })).toBe(false);
    expect(isFrameToHost({ ...ready, theme: { ...theme, position: "top" } })).toBe(false);
    expect(isFrameToHost({ p: PROTOCOL, type: "unread", count: 3 })).toBe(true);
    expect(isFrameToHost({ p: PROTOCOL, type: "unread", count: -1 })).toBe(false);
    expect(isFrameToHost({ p: PROTOCOL, type: "unread", count: 1.5 })).toBe(false);
    expect(isFrameToHost({ p: PROTOCOL, type: "message", sender: "agent" })).toBe(true);
    expect(isFrameToHost({ p: PROTOCOL, type: "message", sender: "visitor" })).toBe(false);
    expect(isFrameToHost({ p: PROTOCOL, type: "close-request" })).toBe(true);
    expect(isFrameToHost({ p: PROTOCOL, type: "nope" })).toBe(false);
    expect(isFrameToHost(null)).toBe(false);
    expect(isFrameToHost([PROTOCOL])).toBe(false);
  });

  it("host messages", () => {
    expect(isHostToFrame({ p: PROTOCOL, type: "set-topic", topic: "support" })).toBe(true);
    expect(isHostToFrame({ p: PROTOCOL, type: "set-topic", topic: 5 })).toBe(false);
    expect(isHostToFrame({ p: PROTOCOL, type: "set-topic", topic: "<img>" })).toBe(false);
    expect(isHostToFrame({ p: PROTOCOL, type: "open" })).toBe(true);
    expect(isHostToFrame({ p: PROTOCOL, type: "close", extra: 1 })).toBe(false);
  });

  it("identify, logout and the identity/bot frame messages", () => {
    const tok = "a.b.c";
    expect(isHostToFrame({ p: PROTOCOL, type: "identify", token: tok })).toBe(true);
    expect(isHostToFrame({ p: PROTOCOL, type: "identify", token: "eyJ-_9.x_-.y0" })).toBe(true);
    expect(isHostToFrame({ p: PROTOCOL, type: "identify", token: "a.b.c=" })).toBe(false);
    expect(isHostToFrame({ p: PROTOCOL, type: "identify", token: "a.b. c" })).toBe(false);
    expect(isHostToFrame({ p: PROTOCOL, type: "identify", token: "a.b" })).toBe(false);
    expect(isHostToFrame({ p: PROTOCOL, type: "identify", token: 5 })).toBe(false);
    expect(isHostToFrame({ p: PROTOCOL, type: "identify", token: tok, extra: 1 })).toBe(false);
    const at = (n: number) => `a.b.${"c".repeat(n - 4)}`;
    expect(isHostToFrame({ p: PROTOCOL, type: "identify", token: at(4096) })).toBe(true);
    expect(isHostToFrame({ p: PROTOCOL, type: "identify", token: at(4097) })).toBe(false);
    expect(isHostToFrame({ p: PROTOCOL, type: "logout" })).toBe(true);
    expect(isHostToFrame({ p: PROTOCOL, type: "logout", x: 1 })).toBe(false);
    expect(isFrameToHost({ p: PROTOCOL, type: "message", sender: "bot" })).toBe(true);
    expect(isFrameToHost({ p: PROTOCOL, type: "identity", verified: true })).toBe(true);
    expect(isFrameToHost({ p: PROTOCOL, type: "identity", verified: "yes" })).toBe(false);
    expect(isFrameToHost({ p: PROTOCOL, type: "identity", verified: true, name: "x" })).toBe(false);
  });

  it("preview config", () => {
    const config = {
      theme: { primary_color: "#112233", position: "left", mode: "dark", launcher_label: null },
      greeting: "Hallo",
      prechat_fields: [
        { key: "name", kind: "text", label: "Name", required: false, options: [], maps_to: "name" },
      ],
      topics: [{ key: "support", label: "Support", queue_id: 3 }],
    };
    expect(isPreviewConfig(config)).toBe(true);
    expect(isHostToFrame({ p: PROTOCOL, type: "preview-config", config })).toBe(true);
    expect(
      isPreviewConfig({ ...config, theme: { ...config.theme, primary_color: "url(x)" } }),
    ).toBe(false);
    expect(
      isPreviewConfig({
        ...config,
        prechat_fields: [{ ...config.prechat_fields[0], kind: "html" }],
      }),
    ).toBe(false);
    expect(isPreviewConfig({ ...config, greeting: "x".repeat(501) })).toBe(false);
  });
});
