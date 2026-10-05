"""Click-to-dial building block: number normalisation and the ARI request."""

from __future__ import annotations

import httpx
import pytest

from tiqora.channels.phone.originate import (
    OriginateConfig,
    OriginateError,
    normalize_dial_number,
    originate,
)

INTERNAL = {"60", "61", "62", "69"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+49 171 7630944", "01717630944"),
        ("0228 / 909 098-70", "022890909870"),
        ("+43 1 234567", "00431234567"),
        ("0041 44 1234567", "0041441234567"),
        ("61", "61"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize_dial_number(raw, INTERNAL) == expected


@pytest.mark.parametrize("raw", ["", "+4930", "0123", "63", "110", "abc", "*0228123456"])
def test_normalize_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_dial_number(raw, INTERNAL)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+49 (0)228 909098-70", "022890909870"),
        ("0049 171 7630944", "01717630944"),
        ("0171/7630944", "01717630944"),
        ("+1 650 253 0000", "0016502530000"),
    ],
)
def test_normalize_with_phonenumbers(raw: str, expected: str) -> None:
    assert normalize_dial_number(raw, INTERNAL) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "0900 1234567",  # premium rate: never dialled
        "+49 900 1234567",
        "0228 12",  # too short for a valid DE number
        "+49 1234",
        "110",
        "112",
    ],
)
def test_normalize_rejects_invalid_and_premium(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_dial_number(raw, INTERNAL)


def test_normalize_region_switches_domestic_format() -> None:
    assert normalize_dial_number("+43 1 234567", INTERNAL, "AT") == "01234567"
    assert normalize_dial_number("+49 171 7630944", INTERNAL, "AT") == "00491717630944"


CFG = OriginateConfig(
    ari_url="http://pbx-asterisk:8088/ari",
    ari_user="tiqora",
    ari_secret="pw",
    context="tiqora-dial",
    endpoint="SIP/{extension}",
    timeout=30,
    internal=frozenset(INTERNAL),
)


async def test_originate_posts_channel_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "1727.1"})

    await originate(CFG, "60", "01717630944", "Kettler", transport=httpx.MockTransport(handler))
    [req] = seen
    assert req.method == "POST"
    assert req.url.path == "/ari/channels"
    params = dict(req.url.params)
    assert params == {
        "endpoint": "SIP/60",
        "extension": "01717630944",
        "context": "tiqora-dial",
        "priority": "1",
        "timeout": "30",
        "callerId": '"Kettler" <01717630944>',
    }
    assert req.headers["authorization"].startswith("Basic ")


async def test_originate_maps_ari_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Endpoint not found")

    with pytest.raises(OriginateError):
        await originate(CFG, "60", "01717630944", "", transport=httpx.MockTransport(handler))


async def test_originate_maps_connection_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(OriginateError):
        await originate(CFG, "60", "01717630944", "", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+49 (0)171 7630944", "01717630944"),
        ("+49(0)228 909098-70", "022890909870"),
    ],
)
def test_normalize_drops_bracketed_trunk_zero(raw: str, expected: str) -> None:
    assert normalize_dial_number(raw, INTERNAL) == expected


@pytest.mark.parametrize("raw", ["0٢٢٨١٢٣٤٥٦", "０２２８１２３４５６", "+４９ 171 7630944"])
def test_normalize_rejects_non_ascii_digits(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_dial_number(raw, INTERNAL)


async def _caller_id(name: str) -> str:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    await originate(CFG, "60", "01717630944", name, transport=httpx.MockTransport(handler))
    return seen[0].url.params["callerId"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Evil <0900>", '"Evil 0900" <01717630944>'),
        ("Bob\\", '"Bob" <01717630944>'),
        ('"a\nb"', '"a b" <01717630944>'),
        ("x" * 100, f'"{"x" * 40}" <01717630944>'),
        ("<>\\\x00", '"01717630944" <01717630944>'),
    ],
)
async def test_originate_sanitises_caller_name(name: str, expected: str) -> None:
    assert await _caller_id(name) == expected


async def test_originate_malformed_endpoint_template() -> None:
    bad = OriginateConfig(**{**CFG.__dict__, "endpoint": "SIP/{ext}"})
    with pytest.raises(OriginateError):
        await originate(
            bad,
            "60",
            "01717630944",
            "",
            transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        )
