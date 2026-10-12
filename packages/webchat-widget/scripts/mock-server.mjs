#!/usr/bin/env node
/**
 * Mock of Tiqora for the widget e2e (no dependencies, Node `http` only). Three loopback
 * ports make the cross-origin setup real:
 *
 *   5190  "Tiqora": serves dist/ under /chat/v1/ with the frame headers of the backend
 *         (webchat/static.py) and an in-memory /api/chat/v1 plus /__test/* hooks
 *   5191  an ALLOWED host page (e2e/host.html embedding the loader), served as
 *         http://localhost:5191 so it is cross-SITE to 127.0.0.1:5190
 *   5192  a host page that is NOT allowed (same HTML; the frame refuses it)
 *
 * Chat assistant (Task 14): POST /__test/bot {on, hold} makes new sessions start in status
 * `bot` (intro line, "typing" ping with who=bot, then one scripted bot line after every
 * visitor line; `hold` parks the bot lines until POST /__test/bot-release). The visitor may
 * POST /handoff. Sign-in: a token is `<hdr>.<base64url JSON {name, sub}>.mocksig`
 * (`.mocksig` verifies, anything else is invalid_identity); POST /sessions {identity},
 * POST /sessions/:id/identify and POST /identity/portal (404 until /__test/portal sets a
 * portal customer) answer like the backend, including 409 identity_conflict for another customer.
 *
 * Run `pnpm build` first. State is in memory; POST /__test/reset clears it.
 */
import { randomBytes, randomUUID } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
const e2eDir = join(root, "e2e");
const TIQORA_PORT = 5190;
const ALLOWED_PORT = 5191;
const DENIED_PORT = 5192;
const TIQORA = `http://127.0.0.1:${TIQORA_PORT}`;
// localhost vs 127.0.0.1 are different SITES, so the widget runs as a real third-party frame.
const ALLOWED_ORIGIN = `http://localhost:${ALLOWED_PORT}`;
const KEY = "wc_e2ekey";
const BOT_NAME = "Robin";
const UPLOAD_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

if (!existsSync(join(dist, "loader.js")) || !existsSync(join(dist, "frame.html"))) {
  console.error("mock-server: dist/ is missing, run `pnpm --filter @tiqora/webchat-widget build` first.");
  process.exit(1);
}

const TYPES = {
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".json": "application/json",
  ".woff2": "font/woff2",
};

/** @type {Map<string, any>} */
let sessions = new Map();
let offline = false;
let lastSession = null;
const stats = { frame200: 0, assets: 0 };
const offlineMessages = [];
let botMode = false;
let botHold = false;
/** @type {(() => void)[]} */
let heldBotLines = [];
let botLines = 0;
let portal = null;

/** The mock's "signed" identity: `<hdr>.<base64url JSON {name, sub}>.mocksig`; null when invalid. */
function parseIdentity(token) {
  if (typeof token !== "string") return null;
  const parts = token.split(".");
  if (parts.length !== 3 || parts[2] !== "mocksig") return null;
  try {
    const o = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
    return typeof o?.sub === "string" ? { sub: o.sub, name: typeof o.name === "string" ? o.name : null } : null;
  } catch {
    return null;
  }
}

function resetState() {
  for (const s of sessions.values()) for (const r of s.streams) r.destroy();
  sessions = new Map();
  offline = false;
  lastSession = null;
  stats.frame200 = 0;
  stats.assets = 0;
  offlineMessages.length = 0;
  botMode = false;
  botHold = false;
  heldBotLines = [];
  botLines = 0;
  portal = null;
}

const json = (res, status, body, headers = {}) => {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers });
  res.end(JSON.stringify(body));
};
const notFound = (res) => json(res, 404, { detail: "not_found" });

function readBody(req) {
  return new Promise((resolveBody, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolveBody(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}
async function readJson(req) {
  try {
    return JSON.parse((await readBody(req)).toString("utf8") || "{}");
  } catch {
    return {};
  }
}

function frameCsp(origin) {
  return (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; font-src 'self'; " +
    `base-uri 'none'; form-action 'none'; object-src 'none'; connect-src ${TIQORA}/api/chat/v1/; ` +
    `frame-ancestors 'self'${origin ? ` ${origin}` : ""}`
  );
}

function sendFile(res, file, headers = {}) {
  const abs = resolve(file);
  if (!abs.startsWith(dist + sep) || !existsSync(abs)) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("not found");
    return;
  }
  res.writeHead(200, { "Content-Type": TYPES[extname(abs)] ?? "application/octet-stream", ...headers });
  res.end(readFileSync(abs));
}

const originOf = (url) => {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
};

function serveFrame(req, res, url) {
  const origin = url.searchParams.get("origin") ?? "";
  const referer = req.headers.referer;
  const refuse = (status, text) => {
    res.writeHead(status, {
      "Content-Type": "text/plain; charset=utf-8",
      "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
      "Cache-Control": "no-store",
      "X-Robots-Tag": "noindex",
    });
    res.end(text);
  };
  if (url.searchParams.get("k") !== KEY) return refuse(404, "Nicht gefunden.");
  if (origin !== ALLOWED_ORIGIN) return refuse(403, "Diese Website ist für den Chat nicht freigegeben.");
  if (referer && originOf(referer) !== origin) return refuse(403, "Diese Website ist für den Chat nicht freigegeben.");
  stats.frame200++;
  res.writeHead(200, {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "X-Robots-Tag": "noindex",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": frameCsp(origin),
  });
  res.end(readFileSync(join(dist, "frame.html")));
}

// ---- chat API --------------------------------------------------------------------------

const live = (s) => s.status === "waiting" || s.status === "active" || s.status === "bot";

function config() {
  return {
    greeting: null,
    privacy_url: null,
    theme: { primary_color: "#0b7a75", position: "right", mode: "light", launcher_label: null },
    topics: [{ key: "general", label: "Allgemein", online: !offline }],
    online: !offline,
    offline_mode: "form",
    portal_identity: portal !== null,
    bot_name: BOT_NAME,
    prechat_fields: [
      { key: "name", kind: "text", label: "Name", required: true, options: [], identity_supplied: true },
      { key: "email", kind: "email", label: "E-Mail", required: false, options: [], identity_supplied: true },
    ],
    upload: { enabled: true, max_bytes: 5 * 1024 * 1024, types: UPLOAD_TYPES },
    max_message_length: 4000,
  };
}

function addMessage(s, m) {
  const msg = {
    seq: s.messages.length + 1,
    sender: "visitor",
    body: null,
    code: null,
    params: {},
    agent_name: null,
    created: new Date().toISOString(),
    attachments: [],
    client_id: null,
    ...m,
  };
  s.messages.push(msg);
  pushEvent(s, "message", msg, msg.seq);
  return msg;
}

function writeEvent(res, event, data, id) {
  res.write(`${id != null ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}
function pushEvent(s, event, data, id) {
  for (const r of s.streams) writeEvent(r, event, data, id);
}
function sessionEvent(s) {
  pushEvent(s, "session", {
    status: s.status,
    agent_name: s.agent_name,
    ...(s.status === "bot" ? { bot_name: BOT_NAME } : {}),
  });
}

/** One scripted AI answer to the latest visitor line: a typing ping, then the line. */
function botTurn(s) {
  if (s.status !== "bot") return;
  s.botPending = true;
  pushEvent(s, "typing", { who: "bot" }); // a stream that connects later is told on connect
  const answer = () => {
    s.botPending = false;
    if (s.status !== "bot") return;
    addMessage(s, { sender: "bot", agent_name: BOT_NAME, body: `Das beantworte ich gern (${++botLines}).` });
  };
  if (botHold) heldBotLines.push(answer);
  else setTimeout(answer, 250);
}

function sessionOut(s) {
  return {
    session_id: s.id,
    status: s.status,
    agent_name: s.agent_name,
    topic_label: null,
    can_send: live(s),
    needs_contact: false,
    last_seq: s.messages.length,
    identified: !!s.identity,
    identified_as: s.identity?.name ?? null,
    bot_name: s.status === "bot" ? BOT_NAME : null,
    can_handoff: s.status === "bot",
  };
}

function parseMultipart(raw, contentType) {
  const boundary = /boundary=(?:"([^"]+)"|([^;]+))/.exec(contentType)?.slice(1).find(Boolean);
  const out = { fields: {}, file: null };
  if (!boundary) return out;
  const marker = Buffer.from(`--${boundary}`);
  let at = raw.indexOf(marker);
  while (at >= 0) {
    const start = at + marker.length;
    if (raw.subarray(start, start + 2).toString() === "--") break;
    const headEnd = raw.indexOf("\r\n\r\n", start);
    if (headEnd < 0) break;
    const head = raw.subarray(start, headEnd).toString("utf8");
    const next = raw.indexOf(marker, headEnd + 4);
    if (next < 0) break;
    const content = raw.subarray(headEnd + 4, next - 2);
    const name = /name="([^"]*)"/.exec(head)?.[1];
    const filename = /filename="([^"]*)"/.exec(head)?.[1];
    if (filename != null) {
      out.file = { filename, type: /Content-Type:\s*([^\r\n]+)/i.exec(head)?.[1] ?? "", data: content };
    } else if (name) out.fields[name] = content.toString("utf8");
    at = next;
  }
  return out;
}

async function api(req, res, url) {
  const path = url.pathname.slice("/api/chat/v1".length);
  const method = req.method;

  if (method === "GET" && path === "/config") {
    if (url.searchParams.get("k") !== KEY) return notFound(res);
    if (url.searchParams.get("origin") !== ALLOWED_ORIGIN) return json(res, 403, { detail: "origin_not_allowed" });
    return json(res, 200, config());
  }

  if (method === "POST" && path === "/offline") {
    const b = await readJson(req);
    if (!b.email) return json(res, 422, { detail: "invalid_prechat", errors: { email: "required" } });
    offlineMessages.push(b);
    return json(res, 200, {});
  }

  if (method === "POST" && path === "/sessions") {
    const b = await readJson(req);
    if (b.key !== KEY) return notFound(res);
    if (!b.message) return json(res, 422, { detail: "invalid_prechat", errors: { message: "required" } });
    let identity = null;
    if (b.identity != null) {
      identity = parseIdentity(b.identity);
      if (!identity) return json(res, 422, { detail: "invalid_identity" });
    }
    const s = {
      id: randomUUID(),
      token: randomBytes(24).toString("base64url"),
      status: botMode ? "bot" : "waiting",
      identity,
      agent_name: null,
      messages: [],
      files: new Map(),
      streams: new Set(),
    };
    sessions.set(s.id, s);
    lastSession = s.id;
    const message = addMessage(s, { sender: "visitor", body: String(b.message), client_id: b.client_id ?? null });
    if (s.status === "bot") {
      addMessage(s, { sender: "system", code: "bot_intro", params: { name: BOT_NAME, notice: null } });
      botTurn(s);
    }
    return json(res, 200, { session_id: s.id, token: `${s.id}.${s.token}`, status: s.status, message });
  }

  if (method === "POST" && path === "/identity/portal") {
    if (portal === null) return notFound(res);
    return json(res, 200, { identity: portal.token, name: portal.name });
  }

  const m = /^\/sessions\/([^/]+)(\/.*)?$/.exec(path);
  if (!m) return notFound(res);
  const s = sessions.get(decodeURIComponent(m[1]));
  const token = req.headers["x-chat-token"];
  if (!s || token !== `${s.id}.${s.token}`) return notFound(res);
  const sub = m[2] ?? "";

  if (method === "GET" && sub === "") return json(res, 200, sessionOut(s));

  if (method === "GET" && sub === "/messages") {
    const after = Number(url.searchParams.get("after")) || 0;
    return json(res, 200, s.messages.filter((x) => x.seq > after));
  }

  if (method === "POST" && sub === "/messages") {
    const b = await readJson(req);
    if (!live(s)) return json(res, 404, { detail: "disabled" });
    const dup = s.messages.find((x) => b.client_id && x.client_id === b.client_id);
    if (dup) return json(res, 200, dup);
    const msg = addMessage(s, { sender: "visitor", body: String(b.body ?? ""), client_id: b.client_id ?? null });
    botTurn(s);
    return json(res, 200, msg);
  }

  if (method === "POST" && sub === "/identify") {
    const b = await readJson(req);
    const who = parseIdentity(b.identity);
    if (!who) return json(res, 422, { detail: "invalid_identity" });
    if (s.identity && s.identity.sub !== who.sub) return json(res, 409, { detail: "identity_conflict" });
    s.identity = who;
    res.writeHead(204, { "Cache-Control": "no-store" });
    return res.end();
  }

  if (method === "POST" && sub === "/handoff") {
    if (s.status !== "bot") return json(res, 409, { detail: "not_bot" });
    s.status = "waiting";
    addMessage(s, { sender: "system", code: "handoff", params: { reason: "visitor" } });
    sessionEvent(s);
    res.writeHead(204, { "Cache-Control": "no-store" });
    return res.end();
  }

  if (method === "POST" && sub === "/typing") return json(res, 200, {});

  if (method === "POST" && sub === "/contact") return json(res, 200, {});

  if (method === "POST" && sub === "/attachments") {
    const { fields, file } = parseMultipart(await readBody(req), req.headers["content-type"] ?? "");
    if (!file || !UPLOAD_TYPES.includes(file.type)) {
      return json(res, 422, { detail: "upload_rejected", errors: { file: "type" } });
    }
    const msg = addMessage(s, {
      client_id: fields.client_id ?? null,
      attachments: [{ index: 0, filename: file.filename, content_type: file.type, size: file.data.length }],
    });
    s.files.set(`${msg.seq}/0`, file);
    return json(res, 200, msg);
  }

  const dl = /^\/messages\/(\d+)\/attachments\/(\d+)$/.exec(sub);
  if (method === "GET" && dl) {
    const file = s.files.get(`${dl[1]}/${dl[2]}`);
    if (!file) return notFound(res);
    res.writeHead(200, { "Content-Type": file.type, "Cache-Control": "no-store" });
    return res.end(file.data);
  }

  if (method === "POST" && sub === "/end") {
    s.status = "ended";
    sessionEvent(s);
    pushEvent(s, "end", { reason: "visitor" });
    return json(res, 200, {});
  }

  if (method === "GET" && sub === "/stream") {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store", Connection: "keep-alive" });
    res.write(": connected\n\n");
    const last = Number(req.headers["last-event-id"]) || 0;
    for (const msg of s.messages) if (msg.seq > last) writeEvent(res, "message", msg, msg.seq);
    s.streams.add(res);
    if (s.botPending) writeEvent(res, "typing", { who: "bot" });
    const beat = setInterval(() => res.write(": hb\n\n"), 10_000);
    res.on("close", () => {
      clearInterval(beat);
      s.streams.delete(res);
    });
    return undefined;
  }

  return notFound(res);
}

async function testHook(req, res, url) {
  const p = url.pathname;
  if (p === "/__test/health") return json(res, 200, { ok: true });
  if (req.method === "GET" && p === "/__test/stats") return json(res, 200, stats);
  if (req.method === "GET" && p === "/__test/last-session") return json(res, 200, { session_id: lastSession });
  if (req.method === "GET" && p === "/__test/session") {
    const s = sessions.get(lastSession);
    if (!s) return notFound(res);
    return json(res, 200, {
      status: s.status,
      identity: s.identity,
      messages: s.messages.map(({ seq, sender, code, body }) => ({ seq, sender, code, body })),
    });
  }
  if (req.method === "GET" && p === "/__test/offline-messages") return json(res, 200, offlineMessages);
  if (req.method !== "POST") return notFound(res);
  const b = await readJson(req);
  if (p === "/__test/reset") {
    resetState();
    return json(res, 200, {});
  }
  if (p === "/__test/offline") {
    offline = !!b.on;
    return json(res, 200, {});
  }
  if (p === "/__test/bot") {
    botMode = !!b.on;
    botHold = !!b.hold;
    return json(res, 200, {});
  }
  if (p === "/__test/bot-release") {
    const run = heldBotLines;
    heldBotLines = [];
    botHold = false;
    for (const answer of run) answer();
    return json(res, 200, { released: run.length });
  }
  if (p === "/__test/portal") {
    portal = b.token ? { token: String(b.token), name: b.name ?? null } : null;
    return json(res, 200, {});
  }
  if (p === "/__test/drop-streams") {
    let n = 0;
    for (const s of sessions.values()) {
      for (const r of [...s.streams]) {
        r.destroy();
        n++;
      }
    }
    return json(res, 200, { dropped: n });
  }
  if (p === "/__test/agent-message") {
    const s = sessions.get(b.session_id ?? lastSession);
    if (!s) return notFound(res);
    if (s.status === "waiting") {
      s.status = "active";
      s.agent_name = "Alex";
      sessionEvent(s);
    }
    return json(res, 200, addMessage(s, { sender: "agent", agent_name: s.agent_name, body: String(b.body ?? "") }));
  }
  return notFound(res);
}

const tiqora = createServer((req, res) => {
  const url = new URL(req.url ?? "/", TIQORA);
  const done = (e) => {
    console.error("mock-server:", e);
    if (!res.headersSent) json(res, 500, { detail: "mock_error" });
    else res.destroy();
  };
  try {
    if (url.pathname.startsWith("/__test/")) return void testHook(req, res, url).catch(done);
    if (url.pathname.startsWith("/api/chat/v1/")) return void api(req, res, url).catch(done);
    if (url.pathname === "/chat/v1/loader.js") {
      return sendFile(res, join(dist, "loader.js"), {
        "Cache-Control": "public, max-age=300",
        "Cross-Origin-Resource-Policy": "cross-origin",
      });
    }
    if (url.pathname === "/chat/v1/frame") return serveFrame(req, res, url);
    if (url.pathname.startsWith("/chat/v1/assets/")) {
      stats.assets++;
      return sendFile(res, join(dist, url.pathname.slice("/chat/v1/".length)), {
        "Cache-Control": "public, max-age=31536000, immutable",
      });
    }
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("not found");
  } catch (e) {
    done(e);
  }
});

const serveHost = (req, res) => {
  const path = new URL(req.url ?? "/", "http://host.invalid").pathname;
  const file = { "/host.html": "host.html", "/host-lang.js": "host-lang.js" }[path];
  if (!file) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    return void res.end("not found");
  }
  res.writeHead(200, { "Content-Type": TYPES[extname(file)], "Cache-Control": "no-store" });
  res.end(readFileSync(join(e2eDir, file)));
};

tiqora.listen(TIQORA_PORT, "127.0.0.1");
// No bind address: "localhost" may resolve to ::1 or 127.0.0.1.
for (const port of [ALLOWED_PORT, DENIED_PORT]) createServer(serveHost).listen(port);
console.log(`mock-server: Tiqora ${TIQORA}, allowed host :${ALLOWED_PORT}, denied host :${DENIED_PORT}`);
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => process.exit(0));
