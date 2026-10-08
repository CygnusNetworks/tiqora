from tiqora.identity.types import RESERVED_SLUGS, SLUG_RE, audience_includes, subject_hash


def test_slug_rules() -> None:
    assert SLUG_RE.match("entra-id")
    assert not SLUG_RE.match("-x") and not SLUG_RE.match("A") and not SLUG_RE.match("a")
    assert "env-oidc" in RESERVED_SLUGS


def test_audience_includes() -> None:
    assert audience_includes("both", "agent") and audience_includes("both", "customer")
    assert audience_includes("agent", "agent") and not audience_includes("agent", "customer")


def test_subject_hash_is_case_sensitive_sha256() -> None:
    assert subject_hash("Abc") != subject_hash("abc")
    assert len(subject_hash("x")) == 64
