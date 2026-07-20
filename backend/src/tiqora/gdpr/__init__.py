"""GDPR tooling: customer anonymization and retention policies.

Both write to Znuny-owned tables (``customer_user``, ``article_data_mime``,
``customer_company``), so both are gated behind schema-ownership being
active (:func:`tiqora.domain.ownership.get_ownership_state`) — or an
explicit, loudly-logged ``force_parallel`` override. See
``docs/gdpr.md`` for the operator-facing walkthrough.
"""

from __future__ import annotations
