import asyncio, json, os, sys
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tiqora.config import get_settings
from tiqora.crypto.secret import decrypt_secret
N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
KEEP = {"[NAME_3]", "[EMAIL_2]"}
async def main():
    s = get_settings()
    e = create_async_engine(os.environ["DATABASE_URL"])
    async with e.connect() as c:
        req, enc, model, pid = (await c.execute(text("select request_json, pii_map_enc, model, provider_id from tiqora_ai_audit_log where id=473"))).first()
        base, key_enc = (await c.execute(text("select base_url, api_key_enc from tiqora_llm_provider where id=:p"), {"p": pid})).first()
    await e.dispose()
    m = json.loads(decrypt_secret(s.secret_key, enc))
    key = decrypt_secret(s.secret_key, key_enc)
    fixed = req
    for ph, val in m.items():
        if ph not in KEEP:
            fixed = fixed.replace(ph, json.dumps(val, ensure_ascii=False)[1:-1])
    fx = json.loads(fixed)
    kb = json.loads(fixed)
    msg = kb["messages"][9]
    head, js = msg["content"].split("\n", 1)
    art = json.loads(js)
    NOTE = (" **Aber:** `aac` zählt nur als Aktivierung, wenn `admin` nicht `mieterliste` ist — "
            "`aac`-Einträge von `mieterliste` (z. B. „Der Mieter … zieht um“) sind Umzugs-/Mietdaten, KEINE Aktivierung. "
            "Maßgeblich ist immer `user.active`: bei `active = 0` ist der Account nicht aktiviert, egal was `history`, "
            "frühere Notizen oder der Kunde sagen; nach einem Umzug braucht der neue Wohnplatz eine neue Registrierung "
            "auf https://startup.stw-bonn.de mit Wohnplatznummer und PKZ.")
    body = art["body"]
    anchor = "Aktivierung = `history`-Event `aac`."
    assert anchor in body, "anchor missing"
    art["body"] = body.replace(anchor, anchor + NOTE)
    msg["content"] = head + "\n" + json.dumps(art)
    variants = {"fixed": fx, "fixed+kb": kb}
    assert "[NAME_7]" not in fixed
    sem = asyncio.Semaphore(4)
    async def one(name, i):
        body = dict(variants[name]); body["model"] = model
        async with sem, httpx.AsyncClient(timeout=180) as h:
            r = await h.post(base.rstrip("/") + "/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
        d = r.json(); msg = d["choices"][0]["message"]
        return {"variant": name, "i": i, "content": msg.get("content"),
                "tool_calls": [{"name": t["function"]["name"], "args": t["function"]["arguments"]} for t in (msg.get("tool_calls") or [])]}
    res = await asyncio.gather(*[one(v, i) for v in variants for i in range(N)], return_exceptions=True)
    print(json.dumps([r if isinstance(r, dict) else {"error": repr(r)} for r in res], ensure_ascii=False))
asyncio.run(main())
