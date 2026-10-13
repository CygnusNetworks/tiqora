"""Safe Perl data-literal parser: the step-0 parser under the names this plan's modules expect.

There is exactly one implementation (``tiqora.znuny.perl_data``, R8); nothing is evaluated.
"""

from __future__ import annotations

from tiqora.znuny.perl_data import PerlDataError as PerlLiteralError
from tiqora.znuny.perl_data import parse_perl_data as parse_perl_literal

__all__ = ["PerlLiteralError", "parse_perl_literal"]
