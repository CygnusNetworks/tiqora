"""Mail domains that say nothing about the sender's organisation.

A ``@gmail.com`` sender is not Google; showing Google's favicon next to them
would be wrong. For these domains the logo lookup answers "no image" without
any outbound request.
"""

from __future__ import annotations

FREEMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "gmx.de",
        "gmx.net",
        "gmx.at",
        "gmx.ch",
        "gmx.com",
        "web.de",
        "t-online.de",
        "outlook.com",
        "outlook.de",
        "msn.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "aol.de",
        "posteo.de",
        "posteo.net",
        "mailbox.org",
        "freenet.de",
        "proton.me",
        "protonmail.com",
        "protonmail.ch",
        "pm.me",
        "arcor.de",
        "1und1.de",
        "online.de",
        "mail.ru",
        "mail.de",
        "email.de",
        "kabelmail.de",
        "vodafonemail.de",
        "tuta.io",
        "tutanota.com",
        "tutanota.de",
        "zoho.com",
        "fastmail.com",
        "hey.com",
        "qq.com",
        "163.com",
        "126.com",
    }
)

# Providers that exist under many country TLDs (hotmail.de, yahoo.co.uk, ...):
# matched on the first label of the domain instead of enumerating TLDs.
FREEMAIL_FIRST_LABELS: frozenset[str] = frozenset(
    {"hotmail", "live", "yahoo", "ymail", "yandex", "outlook", "windowslive"}
)


def is_freemail(domain: str) -> bool:
    """True when *domain* (lowercase, no ``www.``) is a public mail provider."""
    domain = domain.strip().lower().rstrip(".")
    if domain in FREEMAIL_DOMAINS:
        return True
    return domain.split(".", 1)[0] in FREEMAIL_FIRST_LABELS
