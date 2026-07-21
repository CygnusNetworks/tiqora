"""GDPR tooling: customer anonymization, retention, and erasure jobs.

Modules:

* :mod:`tiqora.gdpr.anonymize` — CLI single-customer scrub
* :mod:`tiqora.gdpr.retention` — config-driven ticket-content retention
* :mod:`tiqora.gdpr.erasure` — admin erasure (anonymize/delete + backup/rollback)

All three write to Znuny-owned tables (``customer_user``, ``article_data_mime``,
``customer_company``), so they are gated behind schema-ownership being
active (:func:`tiqora.domain.ownership.get_ownership_state`) — or an
explicit, loudly-logged ``force_parallel`` override. See
``docs/gdpr.md`` for the operator-facing walkthrough.
"""

from __future__ import annotations
