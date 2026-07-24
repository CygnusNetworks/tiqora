"""Unit tests for tiqora.ai.pii.PiiMapper (plan §3.7). No DB, no network."""

from __future__ import annotations

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
    label ("Vertrauensstudenten <vertrauensstudenten@web.de>"), not a person
    — masking it would shred that word everywhere in the ticket text."""
    from tiqora.ai.context import display_name_tokens

    assert display_name_tokens("Vertrauensstudenten <vertrauensstudenten@web.de>") == []
    # Multi-word display names are always kept, even when they mirror the
    # local part — that shape is a real person, not a mailbox label.
    assert display_name_tokens("Anna Meyer <anna.meyer@web.de>") == [
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
