"""Unit tests for tiqora.ai.pii.PiiMapper (plan §3.7). No DB, no network."""

from __future__ import annotations

import time

from tiqora.ai.pii import PiiMapper


def test_mask_unmask_roundtrip_email_phone_mac_ipv4_ipv6() -> None:
    mapper = PiiMapper()
    text = (
        "Contact alice@example.com or +49 30 1234567. "
        "Device 00:1A:2B:3C:4D:5E at 192.168.1.42 and 2001:db8::1."
    )
    masked = mapper.mask(text)
    assert "alice@example.com" not in masked
    assert "+49 30 1234567" not in masked
    assert "00:1A:2B:3C:4D:5E" not in masked
    assert "192.168.1.42" not in masked
    assert "2001:db8::1" not in masked
    assert "[EMAIL_1]" in masked
    assert "[MAC_1]" in masked
    assert "[IPV4_1]" in masked

    restored = mapper.unmask(masked)
    assert restored == text


def test_mask_is_stable_across_calls_same_mapper() -> None:
    mapper = PiiMapper()
    first = mapper.mask("Email alice@example.com again")
    second = mapper.mask("alice@example.com repeats")
    assert "[EMAIL_1]" in first
    assert "[EMAIL_1]" in second
    assert "[EMAIL_2]" not in second


def test_never_mask_set_is_left_untouched() -> None:
    mapper = PiiMapper(never_mask={"192.168.1.1"})
    masked = mapper.mask("Server 192.168.1.1 and 192.168.1.2")
    assert "192.168.1.1" in masked
    assert "[IPV4_1]" in masked
    assert "192.168.1.2" not in masked


def test_mask_empty_and_none_is_safe() -> None:
    mapper = PiiMapper()
    assert mapper.mask(None) == ""
    assert mapper.mask("") == ""
    assert mapper.unmask(None) == ""


def test_unmask_without_prior_mask_is_noop() -> None:
    mapper = PiiMapper()
    assert mapper.unmask("plain text [EMAIL_1]") == "plain text [EMAIL_1]"


def test_dates_are_not_masked_as_phone() -> None:
    mapper = PiiMapper()
    masked = mapper.mask("Termin am 23.07.2026, alternativ 23. 07. 2026 oder im Format 2026-07-23.")
    assert "23.07.2026" in masked
    assert "23. 07. 2026" in masked
    assert "2026-07-23" in masked
    assert "PHONE" not in masked


def test_real_phone_numbers_still_masked() -> None:
    mapper = PiiMapper()
    masked = mapper.mask("Call +49 30 123456 or 030/1234567.")
    assert "+49 30 123456" not in masked
    assert "030/1234567" not in masked
    assert "[PHONE_1]" in masked
    assert "[PHONE_2]" in masked


def test_wp_nummer_is_not_masked_as_phone() -> None:
    """A WP-Nummer must reach the model as digits, not as [PHONE_n] — otherwise
    the agent reports it back as a "Telefonnummer" and cannot use it as the
    search key for the Netadmin tools.

    Both the canonical grouped form (3-2-2-2-1) and the 9-digit core customers
    actually type have to survive.
    """
    mapper = PiiMapper()
    for text in ("WPnum 599-99-99-99-9", "WPnum 5999999999", "WPnum 599999999"):
        assert mapper.mask(text) == text, text


def test_pkz_next_to_wp_nummer_survives_together() -> None:
    """The loose PHONE char class contains spaces and slashes, so an unlabelled
    "PKZ WP-Nummer" pair used to be swallowed into a single PHONE token."""
    mapper = PiiMapper()
    for text in ("900002, 990123456", "900002 990123456", "900002 / 990123456"):
        masked = mapper.mask(text)
        assert "PHONE" not in masked, text
        assert "900002" in masked, text
        assert "990123456" in masked, text


def test_identifier_shaped_number_behind_phone_label_is_still_masked() -> None:
    for text in ("Tel: 990123456", "Fax 990123456", "Durchwahl: 990123456"):
        masked = PiiMapper().mask(text)
        assert "990123456" not in masked, text
        assert "[PHONE_1]" in masked, text


def test_phone_numbers_near_identifier_shapes_still_masked() -> None:
    """Leading zero / "+" / a non-identifier digit group keep a span a phone."""
    for text in ("0201 123456", "030 12345678", "+49 990123456", "(030) 9901234"):
        masked = PiiMapper().mask(text)
        assert "[PHONE_1]" in masked, text


def test_known_names_masked_case_insensitively() -> None:
    mapper = PiiMapper(known_names=["Anna Meyer"])
    masked = mapper.mask("Hi, this is anna meyer writing about my order.")
    assert "anna meyer" not in masked
    assert "[NAME_1]" in masked


def test_known_names_longest_match_wins() -> None:
    mapper = PiiMapper(known_names=["Meyer", "Anna-Lena Meyer"])
    masked = mapper.mask("Anna-Lena Meyer called again.")
    assert "Anna-Lena Meyer" not in masked
    assert masked.count("[NAME_") == 1


def test_known_names_reveal_round_trip() -> None:
    mapper = PiiMapper(known_names=["Anna Meyer"])
    text = "Anna Meyer called about her invoice."
    masked = mapper.mask(text)
    assert mapper.unmask(masked) == text


def test_known_names_respects_never_mask() -> None:
    mapper = PiiMapper(never_mask={"Anna Meyer"}, known_names=["Anna Meyer"])
    masked = mapper.mask("Anna Meyer called again.")
    assert "Anna Meyer" in masked
    assert "[NAME_1]" not in masked


def test_known_names_empty_behaves_like_before() -> None:
    mapper = PiiMapper(known_names=None)
    text = "Anna Meyer called about her invoice."
    assert mapper.mask(text) == text


def test_display_name_tokens_skips_functional_mailbox_labels() -> None:
    """A display name that just mirrors the address local part is a mailbox
    label ("Vertrauensstudenten <vertrauensstudenten@example.org>"), not a person
    — masking it would shred that word everywhere in the ticket text."""
    from tiqora.ai.context import display_name_tokens

    assert display_name_tokens("Vertrauensstudenten <vertrauensstudenten@example.org>") == []
    # Multi-word display names are always kept, even when they mirror the
    # local part — that shape is a real person, not a mailbox label.
    assert display_name_tokens("Anna Meyer <anna.meyer@example.org>") == [
        "Anna Meyer",
        "Anna",
        "Meyer",
    ]
    # A real person whose display name differs from the local part is kept.
    assert display_name_tokens("Anna-Lena Meyer <a.meyer@example.com>") == [
        "Anna-Lena Meyer",
        "Anna-Lena",
        "Meyer",
    ]
    assert display_name_tokens(None) == []


def test_timestamps_are_not_masked_as_ipv6() -> None:
    """Clock times inside ISO timestamps satisfy the loose IPv6 group shape
    ("07:53:55") — they must survive unmasked (run c4fb84d4)."""
    mapper = PiiMapper()
    text = '{"checked_at": "2026-07-24T07:53:55+00:00", "at": "09:05"}'
    assert mapper.mask(text) == text
    # Real IPv6 addresses are still masked.
    masked = mapper.mask("Server fe80::1 and 2001:db8:0:1:1:1:1:1 down")
    assert "fe80::1" not in masked
    assert "2001:db8:0:1:1:1:1:1" not in masked
    assert "[IPV6_" in masked


def test_zulu_timestamps_in_tool_results_are_not_masked_as_ipv6() -> None:
    """A "Z"-terminated ISO timestamp is a word character right after the
    seconds, so the only IPV6 candidate is the bare ":36:" in the middle of
    the time — it used to reach the model as "2026-09-10T14[IPV6_1]36Z"
    (seen in production), leaving the timestamp useless for correlation."""
    mapper = PiiMapper()
    for text in (
        '{"timestamp_utc": "2026-09-10T14:36:36Z"}',
        '{"timestamp_utc": "2026-09-10T14:36:36.123456Z"}',
        "activated 2026-09-10T14:36:36Z, log at 2026-09-10T14:36:41Z",
    ):
        assert mapper.mask(text) == text
    # A real IPv6 next to a Zulu timestamp is still masked.
    masked = mapper.mask('{"at": "2026-09-10T14:36:36Z", "ip": "2a01:238:4d5c::1"}')
    assert "2026-09-10T14:36:36Z" in masked
    assert "2a01:238:4d5c::1" not in masked


def test_json_number_values_are_not_masked_as_phone() -> None:
    """Traffic counters in a diagnose_connection result are JSON numbers;
    JSON carries phone numbers as strings (seen in production)."""
    pii = PiiMapper()
    raw = '{"free_traffic": 6597069766656, "current_traffic": 70793010637}'
    assert pii.mask(raw) == raw
    masked = pii.mask('{"phone": "0228 7654321", "fax": "+49 228 1234567"}')
    assert "0228 7654321" not in masked
    assert "+49 228 1234567" not in masked
    assert "Tel: 70793010637" not in pii.mask("Tel: 70793010637")


def test_generic_name_candidates_are_dropped() -> None:
    from tiqora.ai.context import _is_generic_name

    assert _is_generic_name("User")
    assert _is_generic_name("Invalid User")
    assert _is_generic_name("NetAdmin")
    assert not _is_generic_name("Tobias Beispiel - NetAdmin")
    assert not _is_generic_name("Anna Meyer")
    assert not _is_generic_name("")


def test_pii_never_mask_keeps_ticket_number_readable() -> None:
    from tiqora.ai.context import TicketSnapshot, pii_never_mask

    ticket = TicketSnapshot(
        ticket_id=1,
        queue_id=1,
        customer_id="z90003",
        customer_user_id="z90003#11-invalid",
        title="network",
        ticket_number="2026010510000011",
    )
    never = pii_never_mask(ticket)
    assert never == {"z90003", "z90003#11-invalid", "2026010510000011"}
    pii = PiiMapper(never_mask=never)
    assert pii.mask("[Cygnus#2026010510000011] Re: network") == (
        "[Cygnus#2026010510000011] Re: network"
    )


# ---------------------------------------------------------------------------
# Linear time (docs/superpowers/specs/2026-09-19-pii-mask-linear-time-design.md)
#
# Budgets sit far above the fixed runtime and far below the old quadratic
# one (~40 s for the email case, 54 s for the label case at 256 kB), so they
# neither flake on a loaded runner nor pass on a regression.
# ---------------------------------------------------------------------------

_BIG = 256 * 1024


def _timed_mask(text: str) -> tuple[str, float]:
    started = time.perf_counter()
    out = PiiMapper().mask(text)
    return out, time.perf_counter() - started


def test_a_long_word_run_does_not_backtrack_quadratically() -> None:
    _out, elapsed = _timed_mask("a" * _BIG)
    assert elapsed < 2.0


def test_masking_a_big_text_with_many_candidates_stays_fast() -> None:
    chunk = "Wohnplatz 990123456 ok. "
    text = chunk * (_BIG // len(chunk))
    out, elapsed = _timed_mask(text)
    assert elapsed < 2.0
    assert out == text


def test_masking_a_big_json_result_stays_fast() -> None:
    chunk = '{"free_traffic": 6597069766656}, '
    text = chunk * (_BIG // len(chunk))
    out, elapsed = _timed_mask(text)
    assert elapsed < 2.0
    assert out == text


def test_an_overlong_address_is_never_half_masked() -> None:
    text = "Kontakt " + "a" * 70 + "@example.org bitte"
    out = PiiMapper().mask(text)
    assert out in (text, "Kontakt [EMAIL_1] bitte")


def test_an_address_followed_by_a_hyphen_is_still_masked() -> None:
    assert PiiMapper().mask("Konto erika@example.org-intern") == "Konto [EMAIL_1]-intern"


def test_a_sentence_final_dot_is_not_part_of_the_address() -> None:
    assert PiiMapper().mask("Mail: erika@example.org.") == "Mail: [EMAIL_1]."


def test_only_a_phone_label_close_to_the_number_counts() -> None:
    assert PiiMapper().mask("Wohnplatz 990123456") == "Wohnplatz 990123456"
    text = "Tel: 0228 1234567. " + "x" * 128 + " Wohnplatz 990123456"
    out = PiiMapper().mask(text)
    assert out.startswith("Tel: [PHONE_1]")
    assert out.endswith("Wohnplatz 990123456")
