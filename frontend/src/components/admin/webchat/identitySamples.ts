export type Lang = "node" | "php" | "python";

/** Signing samples for `{email, name, exp}` with HS256; the secret comes from the environment. */
export const IDENTITY_SAMPLES: Record<Lang, string> = {
  node: `const crypto = require("crypto");

const b64 = (obj) => Buffer.from(JSON.stringify(obj)).toString("base64url");

function chatIdentity(email, name) {
  const head = b64({ alg: "HS256", typ: "JWT" });
  const body = b64({ email, name, exp: Math.floor(Date.now() / 1000) + 3600 });
  const sig = crypto
    .createHmac("sha256", process.env.TIQORA_CHAT_SECRET)
    .update(head + "." + body)
    .digest("base64url");
  return head + "." + body + "." + sig;
}

// chatIdentity("erika.muster@example.org", "Erika Muster")`,
  php: `<?php
function b64url(string $data): string {
    return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
}

function chat_identity(string $email, string $name): string {
    $head = b64url(json_encode(['alg' => 'HS256', 'typ' => 'JWT']));
    $body = b64url(json_encode(['email' => $email, 'name' => $name, 'exp' => time() + 3600]));
    $sig = b64url(hash_hmac('sha256', "$head.$body", getenv('TIQORA_CHAT_SECRET'), true));
    return "$head.$body.$sig";
}

// chat_identity('erika.muster@example.org', 'Erika Muster')`,
  python: `import base64, hashlib, hmac, json, os, time

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

def chat_identity(email: str, name: str) -> str:
    head = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url(json.dumps({"email": email, "name": name, "exp": int(time.time()) + 3600}).encode())
    sig = hmac.new(os.environ["TIQORA_CHAT_SECRET"].encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64url(sig)}"

# chat_identity("erika.muster@example.org", "Erika Muster")`,
};
