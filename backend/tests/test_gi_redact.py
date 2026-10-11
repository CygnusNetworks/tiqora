"""Secret masking (pure)."""

from __future__ import annotations

import json

from tiqora.gi.redact import (
    MASK,
    SECRET_KEY_RE,
    redact_config,
    redact_headers,
    redact_text,
    redact_url,
)


def test_redact_headers_and_config() -> None:
    h = redact_headers(
        {
            "Authorization": "Basic dXNlcjpzZWNyZXQ=",
            "Proxy-Authorization": "Basic eA==",
            "X-Api": "Bearer tok-123456",
            "Accept": "application/json",
        },
        secret_values=["tok-123456"],
    )
    assert h["Authorization"] == "Basic ***" and h["Proxy-Authorization"] == "Basic ***"
    assert h["X-Api"] == "Bearer ***" and h["Accept"] == "application/json"
    c = redact_config(
        {
            "Authentication": {
                "AuthType": "BasicAuth",
                "BasicAuthUser": "u",
                "BasicAuthPassword": "pw-secret",
            },
            "Proxy": {"ProxyPassword": "p2-secret", "ProxyHost": "http://proxy.example.org"},
            "Host": "https://x.example.org",
        }
    )
    assert "pw-secret" not in json.dumps(c) and "p2-secret" not in json.dumps(c)
    assert c["Host"] == "https://x.example.org"
    assert c["Proxy"]["ProxyHost"] == "http://proxy.example.org"


def test_redact_headers_secret_names_and_values() -> None:
    h = redact_headers(
        {
            "X-Api-Key": "k-abcdef123",  # name looks secret, no scheme
            "X-Signature": "sig-1234567",
            "Cookie": "sid=abc; other=1",
            "X-Custom": "prefix configured-header-value suffix",
            "X-Short": "abc",
            "Accept": "text/plain",
        },
        secret_values=["configured-header-value", "abc"],  # "abc" is below the 4-char floor
    )
    assert h["X-Api-Key"] == MASK and h["X-Signature"] == MASK and h["Cookie"] == MASK
    assert h["X-Custom"] == "prefix *** suffix"
    assert h["X-Short"] == "abc"
    assert h["Accept"] == "text/plain"


def test_redact_config_new_key_patterns_and_nesting() -> None:
    c = redact_config(
        {
            "ApiKey": "k1",
            "api-key": "k2",
            "X-Api-Foo": "k3",
            "Signature": "sig",
            "Empty": "",
            "PasswordEmpty": "",
            "List": [{"Token": "t1"}, "plain"],
            "Number": 5,
        }
    )
    assert c["ApiKey"] == c["api-key"] == c["X-Api-Foo"] == c["Signature"] == MASK
    assert c["PasswordEmpty"] == ""  # nothing to hide
    assert c["List"] == [{"Token": MASK}, "plain"]
    assert c["Number"] == 5
    assert SECRET_KEY_RE.search("X-Api")


def test_redact_config_does_not_mutate_input() -> None:
    original = {"Password": "pw-secret", "Nested": {"Token": "tok"}}
    redact_config(original)
    assert original == {"Password": "pw-secret", "Nested": {"Token": "tok"}}


def test_redact_url_and_text() -> None:
    assert (
        redact_url("https://u:p@crm.example.org/a?token=abc&x=1")
        == "https://crm.example.org/a?token=***&x=1"
    )
    assert redact_url("https://crm.example.org:8443/a?x=1&api_key=zz") == (
        "https://crm.example.org:8443/a?x=1&api_key=***"
    )
    assert redact_text("failed for pw-secret at host", ["pw-secret"]) == "failed for *** at host"
    assert redact_text("abc", ["ab"]) == "abc"  # values shorter than 4 chars are not masked
    # The longer secret is replaced first, so a shorter one cannot leave a fragment behind.
    assert redact_text("x secret-long y", ["secret", "secret-long"]) == "x *** y"
