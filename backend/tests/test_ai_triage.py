"""Unit tests for the pure half of tiqora.ai.triage: forwarded-sender
extraction and self-consistency confidence. No DB, no LLM."""

from __future__ import annotations

from tiqora.ai.triage import (
    NO_QUEUE_KEY,
    TriageVote,
    aggregate_votes,
    extract_forwarded_sender,
    queue_id_from_key,
    queue_key,
)

# --------------------------------------------------------------------------
# extract_forwarded_sender
# --------------------------------------------------------------------------

OUTLOOK_DE = """\
Hallo Support,

bitte schaut euch das an.

Viele Gruesse
Hausmeister

-----Urspruengliche Nachricht-----
Von: Sven Gras <s27tgras@uni-bonn.de>
Gesendet: Donnerstag, 19. Juni 2026 09:12
An: hausmeister@example.org
Betreff: Internet geht nicht

Mein Anschluss ist seit gestern tot.
"""

OUTLOOK_MAILTO = """\
Weitergeleitet:

-----Original Message-----
Von: Sven Gras [mailto:s27tgras@uni-bonn.de]
An: hausmeister@example.org

Kein Internet.
"""

GMAIL_EN = """\
FYI

---------- Forwarded message ---------
From: Sven Gras <s27tgras@uni-bonn.de>
Date: Thu, 19 Jun 2026 at 09:12
Subject: Internet
To: <hausmeister@example.org>

Kein Internet.
"""

GMAIL_DE = """\
Zur Kenntnis

---------- Weitergeleitete Nachricht ----------
Von: Sven Gras <s27tgras@uni-bonn.de>
Datum: Do., 19. Juni 2026
An: hausmeister@example.org

Kein Internet.
"""

APPLE_MAIL = """\
Siehe unten.

Begin forwarded message:

From: Sven Gras <s27tgras@uni-bonn.de>
Subject: Internet
Date: 19. Juni 2026 um 09:12:00 MESZ
To: hausmeister@example.org

Kein Internet.
"""


def test_outlook_german_marker() -> None:
    found = extract_forwarded_sender(OUTLOOK_DE)
    assert found is not None
    assert found.email == "s27tgras@uni-bonn.de"
    assert found.confidence == 100


def test_outlook_mailto_bracket_form() -> None:
    found = extract_forwarded_sender(OUTLOOK_MAILTO)
    assert found is not None
    assert found.email == "s27tgras@uni-bonn.de"


def test_gmail_english_marker() -> None:
    found = extract_forwarded_sender(GMAIL_EN)
    assert found is not None
    assert found.email == "s27tgras@uni-bonn.de"


def test_gmail_german_marker() -> None:
    found = extract_forwarded_sender(GMAIL_DE)
    assert found is not None
    assert found.email == "s27tgras@uni-bonn.de"


def test_apple_mail_marker() -> None:
    found = extract_forwarded_sender(APPLE_MAIL)
    assert found is not None
    assert found.email == "s27tgras@uni-bonn.de"


def test_quoted_forward_is_unquoted_first() -> None:
    quoted = "\n".join("> " + line for line in OUTLOOK_DE.splitlines())
    found = extract_forwarded_sender(quoted)
    assert found is not None
    assert found.email == "s27tgras@uni-bonn.de"


def test_nbsp_mangled_header_still_parses() -> None:
    mangled = OUTLOOK_DE.replace("Von: ", "Von:\xa0").replace("An: ", "An: ")
    found = extract_forwarded_sender(mangled)
    assert found is not None
    assert found.email == "s27tgras@uni-bonn.de"


def test_nested_forward_takes_outermost_and_lowers_confidence() -> None:
    nested = (
        "Bitte pruefen.\n\n"
        "-----Urspruengliche Nachricht-----\n"
        "Von: Erste Person <erste@uni-bonn.de>\n"
        "An: hausmeister@example.org\n\n"
        "Siehe weiter unten.\n\n"
        "---------- Forwarded message ---------\n"
        "From: Zweite Person <zweite@uni-bonn.de>\n"
        "To: erste@uni-bonn.de\n\n"
        "Kein Internet.\n"
    )
    found = extract_forwarded_sender(nested)
    assert found is not None
    assert found.email == "erste@uni-bonn.de"
    assert found.confidence == 70


def test_plain_reply_chain_is_not_a_forward() -> None:
    reply = (
        "Danke fuer die Rueckmeldung.\n\n"
        "Am 19.06.2026 schrieb Sven Gras <s27tgras@uni-bonn.de>:\n"
        "> Mein Anschluss ist tot.\n"
    )
    assert extract_forwarded_sender(reply) is None


def test_marker_without_from_line_yields_nothing() -> None:
    body = "FYI\n\n-----Urspruengliche Nachricht-----\nBetreff: Internet\n\nKein Internet.\n"
    assert extract_forwarded_sender(body) is None


def test_forwarders_own_address_is_excluded() -> None:
    body = (
        "FYI\n\n-----Urspruengliche Nachricht-----\n"
        "Von: Hausmeister <hausmeister@example.org>\n\nKein Internet.\n"
    )
    assert extract_forwarded_sender(body, exclude=["hausmeister@example.org"]) is None


def test_exclusion_is_case_insensitive() -> None:
    body = (
        "FYI\n\n-----Urspruengliche Nachricht-----\n"
        "Von: Hausmeister <Hausmeister@Example.ORG>\n\nKein Internet.\n"
    )
    assert extract_forwarded_sender(body, exclude=["hausmeister@example.org"]) is None


def test_empty_and_none_bodies() -> None:
    assert extract_forwarded_sender(None) is None
    assert extract_forwarded_sender("") is None


def test_display_name_with_comma_is_parsed() -> None:
    body = (
        "FYI\n\n-----Urspruengliche Nachricht-----\n"
        'Von: "Gras, Sven" <s27tgras@uni-bonn.de>\n\nKein Internet.\n'
    )
    found = extract_forwarded_sender(body)
    assert found is not None
    assert found.email == "s27tgras@uni-bonn.de"


# --------------------------------------------------------------------------
# aggregate_votes
# --------------------------------------------------------------------------

OFFERED = ["q_42", "q_57"]


def _vote(key: str, confidence: int, reason: str = "weil") -> TriageVote:
    return TriageVote(queue_key=key, confidence=confidence, reason=reason)


def test_unanimous_high_self_report() -> None:
    votes = [_vote("q_42", 95)] * 3
    winner, confidence, reason, dist = aggregate_votes(votes, OFFERED)
    assert winner == "q_42"
    assert confidence == 95  # min(agreement=100, self=95)
    assert reason == "weil"
    assert dist[0] == {"key": "q_42", "votes": 3, "self": 95}


def test_unanimous_but_low_self_report_is_capped_by_self_report() -> None:
    votes = [_vote("q_42", 40)] * 3
    _winner, confidence, _reason, _dist = aggregate_votes(votes, OFFERED)
    assert confidence == 40


def test_two_of_three_is_capped_by_agreement() -> None:
    votes = [_vote("q_42", 95), _vote("q_42", 95), _vote("q_57", 90)]
    winner, confidence, _reason, _dist = aggregate_votes(votes, OFFERED)
    assert winner == "q_42"
    assert confidence == 67  # min(agreement=67, self=95)


def test_three_way_split_scores_low() -> None:
    votes = [_vote("q_42", 90), _vote("q_57", 90), _vote(NO_QUEUE_KEY, 90)]
    _winner, confidence, _reason, _dist = aggregate_votes(votes, OFFERED)
    assert confidence <= 33


def test_all_none_is_zero() -> None:
    votes = [_vote(NO_QUEUE_KEY, 90)] * 3
    winner, confidence, _reason, _dist = aggregate_votes(votes, OFFERED)
    assert winner == NO_QUEUE_KEY
    assert confidence == 0


def test_hallucinated_key_is_discarded_but_still_counts_against_k() -> None:
    """A vote for a queue that was never offered must not be silently
    forgiven: it lowers confidence instead."""
    votes = [_vote("q_42", 100), _vote("q_42", 100), _vote("q_999", 100)]
    winner, confidence, _reason, _dist = aggregate_votes(votes, OFFERED)
    assert winner == "q_42"
    assert confidence == 67  # 2 of 3 samples, not 2 of 2


def test_all_votes_hallucinated_yields_none() -> None:
    votes = [_vote("stw-bn", 100)] * 3
    winner, confidence, _reason, _dist = aggregate_votes(votes, OFFERED)
    assert winner == NO_QUEUE_KEY
    assert confidence == 0


def test_no_votes_at_all() -> None:
    winner, confidence, _reason, dist = aggregate_votes([], OFFERED)
    assert winner == NO_QUEUE_KEY
    assert confidence == 0
    assert dist == []


def test_single_sample_uses_self_report_alone() -> None:
    winner, confidence, _reason, _dist = aggregate_votes([_vote("q_42", 88)], OFFERED)
    assert winner == "q_42"
    assert confidence == 88


def test_reason_comes_from_a_winning_vote() -> None:
    votes = [_vote("q_57", 90, ""), _vote("q_57", 90, "netzkoordinator-thema")]
    _winner, _confidence, reason, _dist = aggregate_votes(votes, OFFERED)
    assert reason == "netzkoordinator-thema"


# --------------------------------------------------------------------------
# queue keys
# --------------------------------------------------------------------------


def test_queue_key_roundtrip() -> None:
    assert queue_id_from_key(queue_key(42)) == 42


def test_queue_id_from_key_rejects_foreign_shapes() -> None:
    assert queue_id_from_key("42") is None
    assert queue_id_from_key("stw-bn") is None
    assert queue_id_from_key("q_abc") is None
    assert queue_id_from_key(NO_QUEUE_KEY) is None
