import { describe, expect, it } from "vitest";
import { ChatApiError, createChatApi } from "./api";

type Call = { url: string; init: RequestInit };
function api(res: Response | (() => Response)) {
  const calls: Call[] = [];
  const a = createChatApi({
    fetchImpl: (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return typeof res === "function" ? res() : res;
    }) as unknown as typeof fetch,
  });
  return { a, calls };
}
const j = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers });
const fail = async (p: Promise<unknown>) => {
  try {
    await p;
  } catch (e) {
    return e as ChatApiError;
  }
  throw new Error("did not throw");
};

describe("chat api", () => {
  it("maps errors", async () => {
    let e = await fail(api(j(429, { detail: "rate_limited" }, { "Retry-After": "7" })).a.session("s", "t"));
    expect(e).toBeInstanceOf(ChatApiError);
    expect([e.status, e.code, e.retryAfter]).toEqual([429, "rate_limited", 7]);
    e = await fail(api(j(422, { detail: "invalid_prechat", errors: { email: "email" } })).a.offline({} as never));
    expect(e.errors).toEqual({ email: "email" });
    e = await fail(api(j(422, { detail: "upload_rejected", errors: { file: "size" } })).a.upload("s", "t", new File(["x"], "a.txt"), "c"));
    expect([e.code, e.errors.file]).toEqual(["upload_rejected", "size"]);
    e = await fail(api(j(404, { detail: "disabled" })).a.send("s", "t", "hi", "c"));
    expect([e.status, e.code]).toEqual([404, "disabled"]);
    e = await fail(api(new Response("<html>too big</html>", { status: 413 })).a.send("s", "t", "hi", "c"));
    expect([e.status, e.code]).toEqual([413, "body_too_large"]);
    e = await fail(api(j(422, { detail: [{ loc: ["body"], msg: "x" }] })).a.send("s", "t", "hi", "c"));
    expect(e.code).toBe("invalid");
    e = await fail(api(j(503, { detail: "chat_unavailable" }, { "Retry-After": "30" })).a.config("k", "o"));
    expect(e.retryAfter).toBe(30);
  });

  it("reports network failures as status 0", async () => {
    const a = createChatApi({ fetchImpl: (async () => Promise.reject(new TypeError("x"))) as unknown as typeof fetch });
    const e = await fail(a.session("s", "t"));
    expect([e.status, e.code]).toEqual([0, "network"]);
  });

  it("sends the token as a header and JSON body, never in the URL", async () => {
    const { a, calls } = api(j(201, { seq: 1 }));
    await a.send("sid1", "secret-token", "hello", "cid");
    const c = calls[0];
    expect(c.url).toBe("/api/chat/v1/sessions/sid1/messages");
    expect(c.url).not.toContain("secret-token");
    expect((c.init.headers as Record<string, string>)["X-Chat-Token"]).toBe("secret-token");
    expect((c.init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(c.init.body as string)).toEqual({ body: "hello", client_id: "cid" });
    expect(c.init.credentials).toBe("omit");
  });

  it("builds the other requests", async () => {
    const { a, calls } = api(() => j(200, []));
    await a.config("wc key", "https://h.example");
    await a.messages("s", "t", 5);
    expect(calls[0].url).toBe("/api/chat/v1/config?k=wc%20key&origin=https%3A%2F%2Fh.example");
    expect(calls[1].url).toBe("/api/chat/v1/sessions/s/messages?after=5&limit=200");
    await a.messages("s", "t", Number.NaN);
    expect(calls[2].url).toContain("after=0&");
    expect(a.streamUrl("s")).toBe("/api/chat/v1/sessions/s/stream");
    const up = api(j(201, { seq: 2 }));
    await up.a.upload("s", "t", new File(["x"], "a.txt"), "cid");
    expect(up.calls[0].init.body).toBeInstanceOf(FormData);
    expect((up.calls[0].init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    const dl = api(new Response("bin", { status: 200 }));
    expect((await dl.a.download("s", "t", 3, 0)).size).toBe(3);
    expect(dl.calls[0].url).toBe("/api/chat/v1/sessions/s/messages/3/attachments/0");
    const e = api(new Response(null, { status: 204 }));
    await e.a.end("s", "t");
    await e.a.typing("s", "t");
    await e.a.contact("s", "t", "a@b.de", null);
  });

  it("every call omits credentials except the portal identity; handoff and identify use the token header", async () => {
    const { a, calls } = api(() => new Response(JSON.stringify({ identity: "a.b.c", name: "Erika" }), { status: 200 }));
    await a.config("k", "o");
    await a.session("s", "t");
    await a.handoff("sid 1", "tok");
    await a.identify("s", "tok", "a.b.c");
    await a.createSession({ key: "k", origin: "o", visitor_id: "v".repeat(22), message: "x", identity: "a.b.c" });
    await a.offline({ key: "k", origin: "o", visitor_id: "v".repeat(22), message: "x", email: "e@example.org" } as never);
    expect(calls.every((c) => c.init.credentials === "omit")).toBe(true);
    const h = calls[2];
    expect(h.url).toBe("/api/chat/v1/sessions/sid%201/handoff");
    expect(h.init.method).toBe("POST");
    expect((h.init.headers as Record<string, string>)["X-Chat-Token"]).toBe("tok");
    expect(JSON.parse(calls[3].init.body as string)).toEqual({ identity: "a.b.c" });
    expect(calls[3].url).toBe("/api/chat/v1/sessions/s/identify");
    expect(JSON.parse(calls[4].init.body as string).identity).toBe("a.b.c");
    const out = await a.portalIdentity({ key: "k", origin: "o", visitor_id: "v".repeat(22) });
    expect(out).toEqual({ identity: "a.b.c", name: "Erika" });
    const last = calls[calls.length - 1];
    expect(last.url).toBe("/api/chat/v1/identity/portal");
    expect(last.init.credentials).toBe("same-origin");
    expect(last.init.method).toBe("POST");
  });
});
