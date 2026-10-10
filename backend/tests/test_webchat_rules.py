"""Pure rules of the web chat: origins, CSP, text, uploads, tokens (no DB)."""

from __future__ import annotations

import hashlib
import re

import pytest
from starlette.requests import Request

from tiqora.config import Settings
from tiqora.webchat.models import ChatConfigValues, ChatTopic, UploadPolicy
from tiqora.webchat.origins import (
    OriginError,
    frame_csp,
    normalize_origin,
    origin_allowed,
    own_origin,
    preview_request_allowed,
    referer_origin,
    same_origin_ok,
    validate_origins,
)
from tiqora.webchat.text import (
    CONTROL_CHARS,
    MessageRejected,
    chat_body_from_article,
    clean_page_url,
    normalize_email,
    quoted_display_name,
    sanitize_message,
)
from tiqora.webchat.tokens import (
    hash_ip,
    issue_visitor_token,
    new_session_id,
    parse_visitor_token,
    token_matches,
)
from tiqora.webchat.uploads import (
    UploadRejected,
    check_upload,
    content_length_within,
    is_raster_image,
    safe_upload_filename,
    sniff_upload_type,
)

RLO = "\N{RIGHT-TO-LEFT OVERRIDE}"
LRI = "\N{LEFT-TO-RIGHT ISOLATE}"
PDI = "\N{POP DIRECTIONAL ISOLATE}"
ZWSP = "\N{ZERO WIDTH SPACE}"
OWN = "https://helpdesk.example.org"


# --- origins ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.Example.org", "https://www.example.org"),
        ("https://www.example.org:443/", "https://www.example.org"),
        ("https://shop.example.org:8443", "https://shop.example.org:8443"),
        ("http://localhost:5173", "http://localhost:5173"),
        ("  HTTPS://WWW.EXAMPLE.ORG  ", "https://www.example.org"),
        (
            "https://b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher.example",
            "https://xn--bcher-kva.example",
        ),
        # fullwidth letters are folded by IDNA nameprep, so they cannot spoof a Latin entry
        ("https://\N{FULLWIDTH LATIN SMALL LETTER E}xample.org", "https://example.org"),
    ],
)
def test_normalize_origin_ok(raw: str, expected: str) -> None:
    assert normalize_origin(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://www.example.org",
        "https://www.example.org/path",
        "https://www.example.org?x=1",
        "https://www.example.org#frag",
        "https://user@www.example.org",
        "https://allowed.example.org@evil.example",
        "https://user:pw@www.example.org",
        "https://*.example.org",
        "javascript:alert(1)",
        "https://a.example.org; script-src *",
        "https://a.example.org 'unsafe-inline'",
        "https://a.example.org\nSet-Cookie: x=1",
        "https://a.exam\nple.org",
        "https://a.example.org'",
        'https://a.example.org"',
        "https://a.example.org,https://b.example.org",
        "https://example.org.",
        "https://.example.org",
        "https://a..example.org",
        "https://[::1]",
        "https://example.org:0",
        "https://example.org:99999",
        "https://example.org:abc",
        "https://example.org:",
        "ftp://example.org",
        "//example.org",
        "null",
        "",
        "   ",
        "example.org",
        "https://" + "a" * 64 + ".example.org",
        "https://example.org/" + "a" * 400,
    ],
)
def test_normalize_origin_rejects_bad(raw: str) -> None:
    with pytest.raises(OriginError):
        normalize_origin(raw)


def test_normalized_origin_is_always_csp_safe() -> None:
    for raw in (
        "https://Ex-Ample.org:8443",
        "http://127.0.0.1:80",
        "https://b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher.example",
    ):
        assert re.fullmatch(r"https?://[a-z0-9.-]+(:[0-9]{1,5})?", normalize_origin(raw))


def test_validate_origins_dedupes_and_caps() -> None:
    assert validate_origins(["https://WWW.example.org/", "https://www.example.org"]) == [
        "https://www.example.org"
    ]
    assert validate_origins([]) == []
    with pytest.raises(OriginError):
        validate_origins([f"https://s{i}.example.org" for i in range(21)])
    with pytest.raises(OriginError):
        validate_origins(["https://ok.example.org", "https://x.example.org;"])


def test_values_normalise_origins() -> None:
    v = ChatConfigValues(
        topics=[ChatTopic(key="s", label="S", queue_id=1)],
        allowed_origins=["https://WWW.example.org/", "https://www.example.org"],
    )
    assert v.allowed_origins == ["https://www.example.org"]


def test_origin_allowed_exact_only() -> None:
    allowed = ["https://www.example.org"]
    assert origin_allowed("https://www.example.org", allowed)
    assert origin_allowed("HTTPS://WWW.Example.ORG", allowed)
    assert origin_allowed("https://www.example.org:443", allowed)
    assert not origin_allowed("https://evil.example.org", allowed)
    assert not origin_allowed("https://www.example.org.evil.test", allowed)
    assert not origin_allowed("https://www.example.org.", allowed)
    assert not origin_allowed(
        "https://allowed.example.org@evil.example", ["https://allowed.example.org"]
    )
    assert not origin_allowed("https://www.example.org:8443", allowed)
    assert not origin_allowed("http://www.example.org", allowed)
    assert not origin_allowed("null", allowed)
    assert not origin_allowed(None, allowed) and not origin_allowed("garbage", allowed)
    assert not origin_allowed("", allowed)
    assert not origin_allowed("https://www.example.org", [])
    # a corrupt entry in the stored list never matches and never raises
    assert not origin_allowed("https://www.example.org", ["https://*.example.org"])


def test_origin_allowed_idn_homograph_does_not_match_latin() -> None:
    cyrillic_a = "\N{CYRILLIC SMALL LETTER A}"
    spoof = f"https://{cyrillic_a}llowed.example.org"
    assert normalize_origin(spoof).startswith("https://xn--")
    assert not origin_allowed(spoof, ["https://allowed.example.org"])
    # the same IDN entered in unicode and in punycode is one origin
    assert origin_allowed(spoof, [normalize_origin(spoof)])


def _request(url: str = "http://testserver/x") -> Request:
    m = re.match(r"(https?)://([^/]+)(/.*)", url)
    assert m
    scheme, netloc, path = m.groups()
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "server": (netloc.split(":")[0], 80),
            "path": path,
            "query_string": b"",
            "headers": [(b"host", netloc.encode())],
        }
    )


def test_own_origin() -> None:
    s = Settings(public_base_url="https://helpdesk.example.org/sub")
    assert own_origin(_request(), s) == OWN
    assert own_origin(_request(), Settings(public_base_url="")) == "http://testserver"
    assert own_origin(
        _request("https://Desk.Example.org:8443/x"), Settings(public_base_url="")
    ) == ("https://desk.example.org:8443")
    # a configured value that cannot be an origin falls back to the request
    assert own_origin(_request(), Settings(public_base_url="not a url")) == "http://testserver"


def test_own_origin_refuses_hostile_host_header() -> None:
    with pytest.raises(OriginError):
        own_origin(_request("http://evil.test;x/x"), Settings(public_base_url=""))


# R-ORIGIN: unsafe methods require a same-host Origin
def _ok(
    method: str,
    origins: list[str],
    *,
    sfs: str | None = None,
    host: str | None = "helpdesk.example.org",
) -> bool:
    return same_origin_ok(method, origins, sfs, host, OWN)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_same_origin_unsafe_requires_origin(method: str) -> None:
    assert _ok(method, [OWN])
    assert not _ok(method, [])
    assert not _ok(method, ["null"])
    assert not _ok(method, [""])
    assert not _ok(method, [OWN, OWN])  # duplicated header
    assert not _ok(method, [OWN, "https://evil.example.org"])
    assert not _ok(method, [OWN], sfs="cross-site")
    assert not _ok(method, [OWN], sfs=" Cross-Site ")
    assert _ok(method, [OWN], sfs="same-origin")


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example.org",
        "https://helpdesk.example.org.",
        "https://helpdesk.example.org.evil.test",
        "https://evil.test/https://helpdesk.example.org",
        "https://helpdesk.example.org@evil.test",
        "https://evil.test@helpdesk.example.org",
        "https://helpdesk.example.org:8443",
        "https://helpdesk.example.org/",
        "https://helpdesk.example.org evil.test",
        "https://helpdesk.example.org\n",
        "https://helpdesk.example.org, https://evil.test",
        "helpdesk.example.org",
        "https://sub.helpdesk.example.org",
    ],
)
def test_same_origin_refuses_host_tricks(origin: str) -> None:
    assert not _ok("POST", [origin])


def test_same_origin_host_only_and_case_insensitive() -> None:
    # scheme is not compared (TLS termination), like security/csrf.py
    assert _ok("POST", ["http://helpdesk.example.org"])
    assert _ok("POST", ["HTTPS://HELPDESK.Example.ORG"])
    assert _ok("POST", ["  https://helpdesk.example.org  "])
    assert _ok("POST", ["https://helpdesk.example.org:443"])
    # request Host header is accepted too (no public_base_url configured)
    assert same_origin_ok("POST", ["http://10.0.0.5:8000"], None, "10.0.0.5:8000", "")
    assert not same_origin_ok("POST", ["http://10.0.0.5:8000"], None, "10.0.0.6:8000", "")
    assert not same_origin_ok("POST", ["https://evil.test"], None, None, "")


def test_same_origin_safe_methods() -> None:
    assert _ok("GET", [])
    assert _ok("HEAD", [])
    assert _ok("GET", [OWN])
    assert not _ok("GET", ["null"])
    assert not _ok("GET", ["https://evil.example.org"])
    assert not _ok("GET", [OWN, OWN])


def test_preview_request_allowed() -> None:
    assert preview_request_allowed(OWN, OWN + "/admin/chat-configs?x=1", OWN)
    assert preview_request_allowed("HTTPS://Helpdesk.Example.org", OWN + "/", OWN)
    assert not preview_request_allowed(OWN, None, OWN)  # missing Referer
    assert not preview_request_allowed(OWN, "", OWN)
    assert not preview_request_allowed(OWN, "https://evil.example.org/", OWN)
    assert not preview_request_allowed(OWN, "https://helpdesk.example.org.evil.test/", OWN)
    assert not preview_request_allowed("https://evil.example.org", OWN + "/", OWN)
    assert not preview_request_allowed(None, OWN + "/", OWN)
    assert not preview_request_allowed("garbage", "garbage", OWN)


def test_referer_origin() -> None:
    assert referer_origin("https://A.example.org:443/p?q#f") == "https://a.example.org"
    assert referer_origin("https://u@a.example.org/") is None
    assert referer_origin("javascript:alert(1)") is None
    assert referer_origin(None) is None


# --- frame CSP ----------------------------------------------------------------------------------


def test_frame_csp() -> None:
    allowed = ["https://www.example.org", "https://shop.example.org"]
    csp = frame_csp(allowed, own=OWN, origin="https://www.example.org")
    assert csp.endswith("frame-ancestors 'self' https://www.example.org")
    assert "shop.example.org" not in csp  # R-FRAME-ANCESTORS: only the validated origin
    assert (
        "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "default-src 'none'" in csp
    )
    assert "connect-src https://helpdesk.example.org/api/chat/v1/;" in csp
    assert "connect-src 'self'" not in csp
    assert frame_csp(allowed, own=OWN).endswith("frame-ancestors 'self'")
    assert frame_csp([], own=OWN).endswith("frame-ancestors 'self'")
    prev = frame_csp([], own=OWN, preview_only=True)
    assert prev.endswith("frame-ancestors 'self'")
    assert frame_csp(
        allowed, own=OWN, origin="https://www.example.org", preview_only=True
    ).endswith("frame-ancestors 'self'")


def test_frame_csp_normalises_origin_param() -> None:
    csp = frame_csp(["https://www.example.org"], own=OWN, origin="HTTPS://WWW.Example.org:443")
    assert csp.endswith("frame-ancestors 'self' https://www.example.org")


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example.org",
        "https://www.example.org.evil.test",
        "https://www.example.org; script-src *",
        "https://www.example.org 'unsafe-inline'",
        "https://www.example.org\r\nSet-Cookie: a=b",
        "https://www.example.org'",
        "https://www.example.org;",
        "*",
        "null",
        "",
    ],
)
def test_frame_csp_refuses_bad_or_foreign_origin(origin: str) -> None:
    with pytest.raises(OriginError):
        frame_csp(["https://www.example.org"], own=OWN, origin=origin)


def test_frame_csp_ignores_corrupt_allow_list_entries() -> None:
    csp = frame_csp(
        ["https://x.example.org; img-src *", "https://www.example.org"],
        own=OWN,
        origin="https://www.example.org",
    )
    assert ";" not in csp.split("frame-ancestors", 1)[1]
    assert "img-src *" not in csp


def test_frame_csp_refuses_hostile_own() -> None:
    with pytest.raises(OriginError):
        frame_csp([], own="https://helpdesk.example.org; script-src *")


def test_frame_csp_has_one_directive_per_name() -> None:
    csp = frame_csp(["https://www.example.org"], own=OWN, origin="https://www.example.org")
    names = [d.strip().split(" ")[0] for d in csp.split(";")]
    assert len(names) == len(set(names))
    assert "\n" not in csp and "\r" not in csp


# --- text ---------------------------------------------------------------------------------------


def test_sanitize_message_strips_controls_and_bidi() -> None:
    assert sanitize_message(f"  Hallo\r\nWelt\x07{RLO}  ") == "Hallo\nWelt"
    assert sanitize_message("<script>alert(1)</script>") == "<script>alert(1)</script>"


def test_sanitize_message_keeps_newline_tab_and_normalises_cr() -> None:
    assert sanitize_message("a\rb\tc\nd") == "a\nb\tc\nd"
    assert sanitize_message("a\r\n\r\nb") == "a\n\nb"


@pytest.mark.parametrize(
    "dirty",
    [
        "a\x00b",
        "a\x1bb",
        "a\x7fb",
        "a\x85b",
        "a\x9fb",
        f"a{LRI}b{PDI}",
        f"a{ZWSP}b",
        "a\N{BYTE ORDER MARK}b",
        "a\N{LINE SEPARATOR}b",
        "a\N{ARABIC LETTER MARK}b",
    ],
)
def test_sanitize_message_drops_hidden_characters(dirty: str) -> None:
    out = sanitize_message(dirty)
    assert out == "ab"
    assert not CONTROL_CHARS.search(out)


def test_sanitize_message_drops_lone_surrogates_and_keeps_emoji_and_umlauts() -> None:
    assert sanitize_message("a" + chr(0xD800) + "b") == "ab"
    assert sanitize_message(
        "Gr\N{LATIN SMALL LETTER U WITH DIAERESIS}\N{LATIN SMALL LETTER SHARP S}e \N{GRINNING FACE}"
    ) == (
        "Gr\N{LATIN SMALL LETTER U WITH DIAERESIS}\N{LATIN SMALL LETTER SHARP S}e \N{GRINNING FACE}"
    )
    sanitize_message(chr(0xD83D) + "x").encode("utf-8")  # storable


def test_sanitize_message_rejects() -> None:
    with pytest.raises(MessageRejected) as e1:
        sanitize_message(" \n\t ")
    assert e1.value.code == "empty"
    with pytest.raises(MessageRejected) as e2:
        sanitize_message("x" * 2001)
    assert e2.value.code == "too_long"


@pytest.mark.parametrize("only_hidden", [ZWSP, RLO + PDI, "\x00\x07", " \n "])
def test_sanitize_message_hidden_only_is_empty(only_hidden: str) -> None:
    with pytest.raises(MessageRejected) as e:
        sanitize_message(only_hidden)
    assert e.value.code == "empty"


def test_sanitize_message_length_boundaries_after_cleanup() -> None:
    assert sanitize_message("x" * 2000) == "x" * 2000
    # hidden characters do not count toward the cap
    assert sanitize_message(("x" + RLO) * 2000) == "x" * 2000
    with pytest.raises(MessageRejected):
        sanitize_message("x" * 4001, max_length=4000)
    assert len(sanitize_message("x" * 4000, max_length=4000)) == 4000


def test_chat_body_from_html_article() -> None:
    assert (
        chat_body_from_article("<p>Hallo <b>Welt</b></p>", "text/html; charset=utf-8")
        == "Hallo Welt"
    )
    assert chat_body_from_article("<p>A</p>", "TEXT/HTML") == "A"
    assert chat_body_from_article("a <b>b</b>", "text/plain") == "a <b>b</b>"
    assert len(chat_body_from_article("y" * 3000, "text/plain")) == 3000
    with pytest.raises(MessageRejected):
        chat_body_from_article("y" * 4001, "text/plain")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Anna@Example.ORG", "Anna@example.org"),
        ("a.b+tag@example.org", "a.b+tag@example.org"),
        ("  a@example.org  ", "a@example.org"),
        ("a@b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher.example", "a@xn--bcher-kva.example"),
    ],
)
def test_normalize_email_ok(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "a",
        "a@b",
        "a@@example.org",
        "a@b@example.org",
        '"a b"@example.org',
        "a b@example.org",
        "a<b>@example.org",
        'a"b@example.org',
        "a,b@example.org",
        "a;b@example.org",
        "a(b)@example.org",
        "a`b@example.org",
        ".a@example.org",
        "a.@example.org",
        "a..b@example.org",
        "a@-example.org",
        "a@example-.org",
        "a@example..org",
        "a@example.org.",
        "a@exam_ple.org",
        "a@example.org\nBcc: x@example.org",
        "a@[127.0.0.1]",
        ("x" * 65) + "@example.org",
        "a@" + "b" * 64 + ".org",
        "a@" + ("b." * 130) + "org",
    ],
)
def test_normalize_email_rejects(raw: str | None) -> None:
    assert normalize_email(raw) is None


def test_quoted_display_name() -> None:
    assert quoted_display_name('Anna "Evil" <x@example.org>\\') == "Anna Evil x@example.org"
    assert quoted_display_name(f"A\r\nBcc: x{RLO}") == "A Bcc: x"
    assert quoted_display_name(None) == ""
    assert len(quoted_display_name("n" * 500)) == 100


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.example.org/a?b=1#c", "https://www.example.org/a?b=1#c"),
        ("http://localhost:5173/x", "http://localhost:5173/x"),
        ("javascript:alert(1)", None),
        ("data:text/html,x", None),
        ("//www.example.org/x", None),
        ("https:///x", None),
        ("https://www.example.org/ x", None),
        ("https://www.example.org/\nx", None),
        ("https://www.example.org:99999/", None),
        ("https://www.example.org/" + "a" * 500, None),
        ("", None),
        (None, None),
    ],
)
def test_clean_page_url(raw: str | None, expected: str | None) -> None:
    assert clean_page_url(raw) == expected


# --- uploads ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content,name,expected",
    [
        (b"\x89PNG\r\n\x1a\n" + b"0" * 20, "a.png", "image/png"),
        (b"\xff\xd8\xff\xe0" + b"0" * 20, "a.jpeg", "image/jpeg"),
        (b"GIF89a" + b"0" * 20, "a.gif", "image/gif"),
        (b"GIF87a" + b"0" * 20, "a.gif", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "a.webp", "image/webp"),
        (b"%PDF-1.7\n", "a.pdf", "application/pdf"),
        ("Gr\u00fc\u00dfe\n".encode(), "notiz.txt", "text/plain"),
        (b"a,b\n1,2\n", "t.CSV", "text/plain"),
        (b"line\n", "x.log", "text/plain"),
        # the name never decides when the bytes are a known type
        (b"\x89PNG\r\n\x1a\n" + b"0" * 20, "looks-like.pdf", "image/png"),
        (b"%PDF-1.7\n", "evil.exe", "application/pdf"),
    ],
)
def test_sniff_ok(content: bytes, name: str, expected: str) -> None:
    assert sniff_upload_type(content, name) == expected


def test_sniff_rejects_svg_renamed_png() -> None:
    assert (
        sniff_upload_type(b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "x.png")
        is None
    )
    assert sniff_upload_type(b'<svg xmlns="http://www.w3.org/2000/svg"/>', "x.svg") is None
    assert sniff_upload_type(b"<html><script>x</script></html>", "x.html") is None
    # an HTML document named .txt is valid UTF-8 text: accepted as text/plain on purpose; it is
    # neutralised on download (text/plain + nosniff + CSP sandbox + attachment, Task 7)
    assert sniff_upload_type(b"<html><script>x</script></html>", "x.txt") == "text/plain"


def test_sniff_rejects_binary_txt_and_exe() -> None:
    assert sniff_upload_type(b"MZ\x90\x00\x03", "setup.exe") is None
    assert sniff_upload_type(b"abc\x00def", "x.txt") is None
    assert sniff_upload_type(b"\xff\xfe\xfa", "x.txt") is None
    assert sniff_upload_type(b"", "x.txt") == "text/plain"  # emptiness is check_upload's job
    assert sniff_upload_type(b"plain text", "README") is None
    assert sniff_upload_type(b"plain text", "x.txt.exe") is None
    assert sniff_upload_type(b"plain text", "x.md") is None
    assert sniff_upload_type(b"\xc3\x28", "x.txt") is None  # invalid UTF-8 sequence


def test_sniff_truncated_or_lookalike_magic() -> None:
    assert sniff_upload_type(b"\x89PNG", "a.png") is None
    assert sniff_upload_type(b"RIFF\x00\x00\x00\x00WAVEfmt ", "a.webp") is None
    assert sniff_upload_type(b"GIF88a", "a.gif") is None
    assert sniff_upload_type(b"%PDF", "a.pdf") is None
    assert sniff_upload_type(b" %PDF-1.7", "a.pdf") is None  # leading junk


def test_raster_only_thumbnails() -> None:
    for ok in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        assert is_raster_image(ok)
    for bad in (
        "image/svg+xml",
        "application/pdf",
        "text/plain",
        "text/html",
        "image/png; x=y",
        "",
    ):
        assert not is_raster_image(bad)


def test_safe_upload_filename() -> None:
    assert safe_upload_filename("../../etc/passwd", "text/plain") == "passwd.txt"
    assert safe_upload_filename("C:\\Users\\x\\Bild.PNG", "image/png") == "Bild.png"
    assert safe_upload_filename(f"rech{RLO}nung.exe.pdf", "application/pdf") == "rechnung.exe.pdf"
    assert safe_upload_filename("", "image/jpeg") == "datei.jpg"
    assert len(safe_upload_filename("a" * 300 + ".pdf", "application/pdf")) == 120


@pytest.mark.parametrize(
    "name,ctype,expected",
    [
        ("..", "text/plain", "datei.txt"),
        (".", "text/plain", "datei.txt"),
        ("a/b/", "text/plain", "datei.txt"),
        ("/", "image/png", "datei.png"),
        (".htaccess", "text/plain", "htaccess.txt"),
        ("evil.exe", "image/png", "evil.png"),
        ("photo.jpeg", "image/jpeg", "photo.jpg"),
        ("a.csv", "text/plain", "a.txt"),
        ("noext", "application/pdf", "noext.pdf"),
        ("a\x00b.png\x00.exe", "image/png", "ab.png.png"),
        ("a\r\nb\t c.png", "image/png", "a b c.png"),
        ('a"b<c>d|e?f*g:h.png', "image/png", "a_b_c_d_e_f_g_h.png"),
        ("  spaced   name  .png", "image/png", "spaced name.png"),
        ("...png", "image/png", "datei.png"),
        ("name. . .png", "image/png", "name.png"),
        ("a.png/../../b.png", "image/png", "b.png"),
        ("C:/x\\y/z.PDF", "application/pdf", "z.pdf"),
        (f"{ZWSP}{LRI}x{PDI}.png", "image/png", "x.png"),
    ],
)
def test_safe_upload_filename_adversarial(name: str, ctype: str, expected: str) -> None:
    assert safe_upload_filename(name, ctype) == expected


def test_safe_upload_filename_is_bounded_in_chars_and_bytes() -> None:
    out = safe_upload_filename("\N{GRINNING FACE}" * 300 + ".png", "image/png")
    assert out.endswith(".png")
    assert len(out) <= 120 and len(out.encode("utf-8")) <= 240
    out2 = safe_upload_filename("x" * 5000, "application/pdf")
    assert out2 == "x" * 116 + ".pdf"
    # unknown type: no extension is invented, still bounded
    assert len(safe_upload_filename("y" * 400, "application/zip")) == 120


def test_safe_upload_filename_never_contains_separators_or_controls() -> None:
    nasty = "a/b\\c\x00d\ne\rf" + RLO + '\x1b[31m<>:"|?*'
    out = safe_upload_filename(nasty, "image/png")
    assert not re.search(r"[/\\<>:\"|?*]", out)
    assert not CONTROL_CHARS.search(out)


def test_check_upload_policy() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    assert check_upload(png, "x.png", UploadPolicy()) == ("x.png", "image/png")
    for policy, code in (
        (UploadPolicy(enabled=False), "disabled"),
        (UploadPolicy(types=["application/pdf"]), "type"),
    ):
        with pytest.raises(UploadRejected) as e:
            check_upload(png, "x.png", policy)
        assert e.value.code == code
    with pytest.raises(UploadRejected) as e:
        check_upload(
            b"%PDF-" + b"0" * (2 * 1024 * 1024), "x.pdf", UploadPolicy(max_bytes=1024 * 1024)
        )
    assert e.value.code == "size"


def test_check_upload_empty_type_and_boundary() -> None:
    with pytest.raises(UploadRejected) as e:
        check_upload(b"", "x.txt", UploadPolicy())
    assert e.value.code == "empty"
    with pytest.raises(UploadRejected) as e2:
        check_upload(b"<svg onload=alert(1)/>", "x.png", UploadPolicy())
    assert e2.value.code == "type"
    one_mib = 1024 * 1024
    ok = b"%PDF-" + b"0" * (one_mib - 5)
    assert check_upload(ok, "x.pdf", UploadPolicy(max_bytes=one_mib))[1] == "application/pdf"
    with pytest.raises(UploadRejected) as e3:
        check_upload(ok + b"0", "x.pdf", UploadPolicy(max_bytes=one_mib))
    assert e3.value.code == "size"
    # declared name cannot smuggle a type that the policy forbids
    with pytest.raises(UploadRejected) as e4:
        check_upload(b"just text", "x.txt", UploadPolicy(types=["image/png"]))
    assert e4.value.code == "type"


def test_check_upload_renames_to_canonical_extension() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 10
    assert check_upload(png, "../../virus.exe", UploadPolicy()) == ("virus.png", "image/png")


def test_upload_policy_ceiling_is_25_mib() -> None:
    with pytest.raises(ValueError):
        UploadPolicy(max_bytes=25 * 1024 * 1024 + 1)
    assert UploadPolicy(max_bytes=25 * 1024 * 1024).max_bytes == 25 * 1024 * 1024


@pytest.mark.parametrize(
    "raw,limit,ok",
    [
        ("100", 100, True),
        ("101", 100, False),
        ("1", 100, True),
        ("0", 100, False),
        (None, 100, False),
        ("", 100, False),
        (" 100 ", 100, True),
        ("-1", 100, False),
        ("+5", 100, False),
        ("1_0", 100, False),
        ("1e2", 100, False),
        ("0x10", 100, False),
        ("10, 10", 100, False),
        ("\N{FULLWIDTH DIGIT ONE}\N{FULLWIDTH DIGIT ZERO}", 100, False),
        ("9" * 30, 100, False),
        ("1.5", 100, False),
    ],
)
def test_content_length_within(raw: str | None, limit: int, ok: bool) -> None:
    assert content_length_within(raw, limit) is ok


def test_content_length_zero_allowed_for_bodyless_json() -> None:
    assert content_length_within("0", 65536, minimum=0)
    assert not content_length_within(None, 65536, minimum=0)


# --- tokens -------------------------------------------------------------------------------------


def test_visitor_token_round_trip() -> None:
    sid = new_session_id()
    token, digest = issue_visitor_token(sid)
    parsed = parse_visitor_token(token)
    assert parsed is not None and parsed[0] == sid and token_matches(parsed[1], digest)
    assert not token_matches(parsed[1], "0" * 64) and not token_matches(parsed[1], "")
    for bad in (None, "", "abc", f"{sid}", f"{sid}.", "Z" * 32 + ".x" * 20, f"{sid}.{'a' * 200}"):
        assert parse_visitor_token(bad) is None


def test_token_format_and_storage() -> None:
    sid = new_session_id()
    assert re.fullmatch(r"[0-9a-f]{32}", sid)
    token, digest = issue_visitor_token(sid)
    head, _, secret = token.partition(".")
    assert head == sid and len(secret) == 43  # token_urlsafe(32)
    assert digest == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in digest and token not in digest
    assert issue_visitor_token(sid)[0] != token  # fresh secret every time
    assert new_session_id() != new_session_id()


def test_token_matches_rejects_other_sessions_secret() -> None:
    sid_a, sid_b = new_session_id(), new_session_id()
    tok_a, dig_a = issue_visitor_token(sid_a)
    tok_b, dig_b = issue_visitor_token(sid_b)
    pa, pb = parse_visitor_token(tok_a), parse_visitor_token(tok_b)
    assert pa and pb
    assert token_matches(pa[1], dig_a) and token_matches(pb[1], dig_b)
    assert not token_matches(pa[1], dig_b) and not token_matches(pb[1], dig_a)
    # the digest itself is not a valid secret
    assert not token_matches(dig_a, dig_a)
    # case-folded or padded digests do not match
    assert not token_matches(pa[1], dig_a.upper())
    assert not token_matches(pa[1], dig_a + " ")
    assert not token_matches(pa[1], dig_a[:-1])
    # non-ASCII input must not raise
    assert not token_matches(
        "\N{GREEK SMALL LETTER ALPHA}" * 30, "\N{GREEK SMALL LETTER ALPHA}" * 64
    )


def test_token_compare_is_constant_time_primitive(monkeypatch: pytest.MonkeyPatch) -> None:
    import hmac as real_hmac

    from tiqora.webchat import tokens

    calls: list[tuple[bytes, bytes]] = []
    original = real_hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(tokens.hmac, "compare_digest", spy)
    _, digest = issue_visitor_token(new_session_id())
    assert not token_matches("s" * 43, digest)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "bad",
    [
        "A" * 32 + "." + "a" * 43,  # uppercase sid
        "g" * 32 + "." + "a" * 43,  # not hex
        "a" * 31 + "." + "a" * 43,
        "a" * 33 + "." + "a" * 43,
        "a" * 32 + "." + "a" * 19,
        "a" * 32 + "." + "a" * 101,
        "a" * 32 + ".a+b" + "a" * 40,
        "a" * 32 + ".a=b" + "a" * 40,
        "a" * 32 + ".a/b" + "a" * 40,
        "a" * 32 + "." + "a" * 43 + "\n",
        "a" * 32 + "." + "a" * 43 + " ",
        " " + "a" * 32 + "." + "a" * 43,
        "a" * 32 + "." + "a" * 20 + "." + "a" * 20,
        "a" * 32 + ".." + "a" * 43,
        "Bearer " + "a" * 32 + "." + "a" * 43,
        "a" * 32 + "." + "\N{GREEK SMALL LETTER ALPHA}" * 43,
        "a" * 5000,
    ],
)
def test_parse_visitor_token_rejects(bad: str) -> None:
    assert parse_visitor_token(bad) is None


def test_parse_visitor_token_bounds_accepted() -> None:
    sid = "a" * 32
    assert parse_visitor_token(f"{sid}.{'b' * 20}") == (sid, "b" * 20)
    assert parse_visitor_token(f"{sid}.{'B_-' * 33}x") == (sid, "B_-" * 33 + "x")
    assert parse_visitor_token(f"{sid}.{'b' * 100}") is not None


# --- hash_ip ------------------------------------------------------------------------------------


def test_hash_ip_is_keyed_and_short() -> None:
    a = hash_ip("192.0.2.1", "k1")
    assert a == hash_ip("192.0.2.1", "k1")
    assert re.fullmatch("[0-9a-f]{32}", a)
    assert a != hash_ip("192.0.2.1", "k2")
    assert a != hash_ip("192.0.2.2", "k1")


def test_hash_ip_does_not_use_the_secret_directly() -> None:
    import hmac

    direct = hmac.new(b"k1", b"webchat-ip:192.0.2.1", hashlib.sha256).hexdigest()[:32]
    assert hash_ip("192.0.2.1", "k1") != direct


def test_hash_ip_ipv4_mapped_ipv6_equals_ipv4() -> None:
    v4 = hash_ip("192.0.2.1", "k")
    assert hash_ip("::ffff:192.0.2.1", "k") == v4
    assert hash_ip("::FFFF:c000:201", "k") == v4
    assert hash_ip(" 192.0.2.1 ", "k") == v4


def test_hash_ip_ipv6_collapses_to_slash_64() -> None:
    base = hash_ip("2001:db8:1:2::1", "k")
    assert hash_ip("2001:db8:1:2:ffff:ffff:ffff:ffff", "k") == base
    assert hash_ip("2001:0DB8:0001:0002:0:0:0:5", "k") == base
    assert hash_ip("2001:db8:1:2::1%eth0", "k") == base
    assert hash_ip("2001:db8:1:3::1", "k") != base
    assert hash_ip("2001:db8:2:2::1", "k") != base
    assert hash_ip("2001:db8:1:2::1", "k2") != base


def test_hash_ip_garbage_does_not_raise_and_does_not_collide_with_real_ips() -> None:
    a = hash_ip("unknown", "k")
    assert re.fullmatch("[0-9a-f]{32}", a)
    assert a != hash_ip("192.0.2.1", "k")
    assert hash_ip("", "k") == hash_ip("   ", "k")
    assert hash_ip("999.1.1.1", "k") != hash_ip("192.0.2.1", "k")
    assert len(hash_ip("x" * 10000, "k")) == 32


# --- IDNA 2008 / UTS-46 (browser behaviour), public_base_url with a path -------------------------

SHARP_S = "\N{LATIN SMALL LETTER SHARP S}"
CAPITAL_SHARP_S = "\N{LATIN CAPITAL LETTER SHARP S}"


def test_origin_sharp_s_matches_browser_punycode_not_ss() -> None:
    stored = normalize_origin(f"https://stra{SHARP_S}e.de")
    assert stored == "https://xn--strae-oqa.de"
    assert origin_allowed("https://xn--strae-oqa.de", [stored])
    assert not origin_allowed("https://strasse.de", [stored])
    assert normalize_origin(f"https://STRA{CAPITAL_SHARP_S}E.de") == "https://xn--strae-oqa.de"
    assert normalize_origin("https://STRASSE.de") == "https://strasse.de"
    assert normalize_origin("https://XN--STRAE-OQA.de") == "https://xn--strae-oqa.de"


@pytest.mark.parametrize(
    "host",
    [
        "a\N{ZERO WIDTH JOINER}b.example",
        "xn--.example",
        "xn--a-.example",
    ],
)
def test_origin_refuses_invisible_or_malformed_idn_hosts(host: str) -> None:
    with pytest.raises(OriginError):
        normalize_origin(f"https://{host}")


def test_email_sharp_s_domain_is_never_rewritten_to_ss() -> None:
    assert normalize_email(f"kunde@stra{SHARP_S}e.de") == "kunde@xn--strae-oqa.de"
    assert normalize_email(f"kunde@STRA{CAPITAL_SHARP_S}E.DE") == "kunde@xn--strae-oqa.de"
    assert normalize_email("kunde@strasse.de") == "kunde@strasse.de"
    assert normalize_email(f"kunde@stra{SHARP_S}e.de") != normalize_email("kunde@strasse.de")
    assert normalize_email("a@a\N{ZERO WIDTH JOINER}b.org") is None


def test_zero_width_space_is_mapped_away_like_a_browser_does() -> None:
    # UTS-46 ignores U+200B; browsers then send the plain host, so the stored form matches it
    assert normalize_origin("https://exa\N{ZERO WIDTH SPACE}mple.org") == "https://example.org"


@pytest.mark.parametrize(
    "pbu",
    [
        "https://helpdesk.example.org/sub",
        "https://helpdesk.example.org/sub/",
        "https://helpdesk.example.org/",
    ],
)
def test_same_origin_public_base_url_with_path_and_rewritten_host(pbu: str) -> None:
    # the proxy rewrote Host to the upstream name: only public_base_url can vouch for the origin
    assert same_origin_ok("POST", [OWN], None, "tiqora-api:8000", pbu)
    assert not same_origin_ok("POST", ["https://evil.example.org"], None, "tiqora-api:8000", pbu)
    settings = Settings(public_base_url=pbu)
    assert own_origin(_request(), settings) == OWN


def test_same_origin_invalid_public_base_url_falls_back_to_host() -> None:
    assert not same_origin_ok("POST", [OWN], None, "tiqora-api:8000", "not a url")
    assert same_origin_ok("POST", [OWN], None, "helpdesk.example.org", "not a url")


def test_control_chars_drop_unicode_tag_characters() -> None:
    assert sanitize_message("hi\U000e0049\U000e0047 there") == "hi there"


def test_token_matches_survives_lone_surrogate() -> None:
    assert not token_matches("a" * 43, chr(0xD800) * 64)
    assert not token_matches(chr(0xD800) * 43, "0" * 64)


def test_safe_upload_filename_removed_control_leaves_single_space() -> None:
    assert safe_upload_filename("a \x00 b.png", "image/png") == "a b.png"
