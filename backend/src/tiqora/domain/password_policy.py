"""Length policy for passwords this product sets itself.

One module so the setup link, the admin user form and the admin customer form
cannot drift apart, and so the frontend has a single source to mirror.

The bounds follow NIST SP 800-63B: a floor well above the 8-character legal
minimum, and a ceiling high enough that passphrases fit (the standard asks
verifiers to permit at least 64 characters — so 64 is the ceiling, not a
number pulled from the air).

Nothing in the Znuny schema constrains this: ``users.pw`` is VARCHAR(255) and
stores a fixed 58-character ``BCRYPT:cost:salt:hash`` string no matter how
long the password was. The one real limit is bcrypt's own — it consumes at
most 72 bytes of key material — and that is handled in
:mod:`tiqora.znuny.password`, not here.
"""

from __future__ import annotations

from typing import Final

MIN_PASSWORD_LENGTH: Final[int] = 12
MAX_PASSWORD_LENGTH: Final[int] = 64


class PasswordPolicyError(ValueError):
    """Raised when a chosen password is outside the permitted length.

    The message is user-facing: API layers pass it straight through as the
    422 detail rather than inventing their own wording.
    """


def validate_password(password: str) -> None:
    """Raise :class:`PasswordPolicyError` unless *password* is within bounds.

    Measured in characters, not bytes — what the person typed is what gets
    counted, so an umlaut costs the same as a letter.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters")
