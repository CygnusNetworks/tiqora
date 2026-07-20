"""Znuny behavioural invariants and compatibility helpers (no Znuny source).

Licensing: unlike the rest of Tiqora (AGPL-3.0-only), the modules in this
package are dual-licensed **AGPL-3.0-only OR GPL-3.0-only** at the
recipient's choice, because they closely track the behaviour of the
GPL-3.0-licensed Znuny 6.5 implementation. See NOTICE.md at the repository
root for details.
"""

from tiqora.znuny.password import detect_scheme, hash_password, verify_password
from tiqora.znuny.sysconfig import ZNUNY_SETTING_DEFAULTS, SysConfig

__all__ = [
    "ZNUNY_SETTING_DEFAULTS",
    "SysConfig",
    "detect_scheme",
    "hash_password",
    "verify_password",
]
