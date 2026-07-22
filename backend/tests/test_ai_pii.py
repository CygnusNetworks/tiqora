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
