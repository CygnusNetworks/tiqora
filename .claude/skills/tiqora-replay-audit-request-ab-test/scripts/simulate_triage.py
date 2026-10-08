"""Dry-run triage simulation: source queue forced to studentenwerk-bonn (10),
first customer article, real masking + prompt + model. No audit, no persist."""

import asyncio
import dataclasses
import json
import os
import sys
from collections import Counter

from tiqora.ai import triage as T
from tiqora.ai.context import collect_known_names, load_articles, pii_never_mask, ticket_snapshot
from tiqora.ai.kb_wiring import build_llm_client
from tiqora.ai.pii import PiiMapper
from tiqora.ai.policies import get_queue_policy_by_queue
from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory

SAMPLES = int(sys.argv[1]) if len(sys.argv) > 1 else 6
VARIANTS = json.loads(os.environ.get("VARIANTS_JSON", "{}"))
# (ticket_id, expected queue key: "none" = stays in 10, "q_5" = stw-bn)
CASES = [tuple(c) for c in json.loads(os.environ["CASES_JSON"])]  # [(tn, expected), ...]
SOURCE_QUEUE = 10


async def run_case(session, settings, policy, base_candidates, tn, variant):
    from sqlalchemy import text
    ticket_id = (await session.execute(text("SELECT id FROM ticket WHERE tn=:tn"), {"tn": tn})).scalar_one()
    ticket = await ticket_snapshot(session, ticket_id)
    ticket = dataclasses.replace(ticket, queue_id=SOURCE_QUEUE, queue_name="studentenwerk-bonn")
    articles = await load_articles(session, ticket_id)
    article = next((a for a in articles if a.sender_type == "customer"), None)
    if article is None:
        return {"ticket": ticket_id, "error": "no customer article"}
    known = await collect_known_names(session, ticket, articles)
    pii = PiiMapper(never_mask=pii_never_mask(ticket), known_names=known)

    cands = base_candidates
    if variant:
        cands = [
            dataclasses.replace(c, description=variant.get(f"q{c.queue_id}", c.description))
            for c in base_candidates
        ]
    raw = await build_llm_client(
        session, settings, policy.triage_llm_provider_id or policy.llm_provider_id,
        policy.triage_model_override or policy.model_override, policy.llm_fallback_json,
    )

    class P:  # minimal policy view for decide()
        routing_description = (variant or {}).get("q10", policy.routing_description)
        triage_samples = SAMPLES

    d = await T.decide(
        session, llm=raw, raw_llm=raw, policy=P, ticket=ticket, article=article,
        candidates=cands, samples=SAMPLES, mask=pii.mask,
    )
    q = d.queue
    got = f"q_{q.queue_id}" if q.queue_id else "none"
    return {
        "tn": tn, "ticket": ticket_id, "article": article.id, "from": article.from_address,
        "subject": (article.subject or "")[:70], "winner": got, "conf": q.confidence,
        "dist": q.candidates, "reason": q.reason, "error": d.error,
    }


async def main():
    settings = get_settings()
    async with get_session_factory()() as session:
        policy = await get_queue_policy_by_queue(session, SOURCE_QUEUE)
        base = await T.load_candidates(session, policy)
        variants = ({} if os.environ.get("SKIP_PROD") else {"prod": None}) | VARIANTS
        for vname, v in variants.items():
            print(f"\n######## VARIANT {vname}")
            ok = 0
            for tid, expected in CASES:
                try:
                    r = await run_case(session, settings, policy, base, tid, v)
                except Exception as exc:  # noqa: BLE001
                    r = {"tn": tid, "error": repr(exc)}
                hit = r.get("winner") == expected
                ok += hit
                print(json.dumps({**r, "expected": expected, "OK": hit}, ensure_ascii=False))
            print(f"######## {vname}: {ok}/{len(CASES)} correct")
        await session.rollback()


asyncio.run(main())
