"""ITSM: incident/problem/service features, CI provider contract and the shared link types.

What lives where (wave 4; the step-0 commit created the shared contracts, the ITSM core plan
owns them, the CMDB plan implements Mode B against them):

* ``models``             — ORM for ``tiqora_itsm_*`` tables (``TiqoraItsmLinkType`` from step 0).
* ``ci_provider``        — CI dataclasses, ``CiProvider`` protocol and errors (pure contract).
* ``providers``          — ``CI_PROVIDERS`` extension point and ``CiProviderFactory``.
* ``mode``               — ITSM mode types and the ``tiqora_settings`` keys that hold the mode.
* ``link_type_defaults`` — the Tiqora default link types and their insert-if-absent seeder.

The Perl data-literal parser used for Znuny class definitions is ``tiqora.znuny.perl_data``.
"""
