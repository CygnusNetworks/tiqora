"""Conformance kit for ``CiProvider`` implementations (design I3; owner: ITSM core).

Importable without any Mode A fixture, database or container: an implementation (the Mode A
adapter, the in-memory fake, the Mode B CMDB provider) subclasses :class:`CiProviderContract` in
its own ``test_*`` module and overrides the three fixtures below. The class is not collected
here (neither the module nor the class name matches pytest's patterns).

The subclass supplies:

* ``provider``  -- the implementation under test;
* ``session``   -- the (async) session passed to every call;
* ``seed``      -- builds the data set described by :class:`SeededCis`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tiqora.itsm.ci_provider import (
    CiError,
    CiNotFound,
    CiPage,
    CiProvider,
    CiQuery,
    CiRef,
    CiSource,
)


@dataclass(frozen=True, slots=True)
class SeededCis:
    """What ``seed`` must create.

    Two CIs (``a``, ``b``) readable by everybody, one (``hidden``) unreadable for
    ``restricted_user_id`` (user ``itsm94-ro``), all three readable for ``user_id``.
    ``a`` has ``a_version_count`` (>= 3) versions. ``common_text`` matches exactly the three CIs
    by name or number for ``user_id``. ``service_id`` has ``a`` (operational) and ``b`` (incident)
    linked; ``empty_service_id`` has none. ``ticket_id`` is a ticket without CI links.
    """

    user_id: int
    restricted_user_id: int
    a: CiRef
    b: CiRef
    hidden: CiRef
    common_text: str
    a_version_count: int
    ticket_id: int
    service_id: int
    empty_service_id: int


class _NoCommitSession:
    """Proxy that fails the test when a provider ends or opens a transaction."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name in {"commit", "rollback", "begin", "close", "begin_nested"}:
            raise AssertionError(f"provider called session.{name}()")
        return getattr(self._inner, name)


def _other(source: CiSource) -> CiSource:
    return "tiqora" if source == "znuny" else "znuny"


class CiProviderContract:
    """Base class of the conformance tests. Override the three fixtures."""

    @pytest.fixture
    def provider(self) -> CiProvider:
        raise NotImplementedError("override the `provider` fixture")

    @pytest.fixture
    def session(self) -> Any:
        raise NotImplementedError("override the `session` fixture")

    @pytest.fixture
    async def seed(self, session: Any) -> SeededCis:
        raise NotImplementedError("override the `seed` fixture")

    # -- identity / get ---------------------------------------------------------------------

    async def test_provider_declares_source_and_mode(self, provider: CiProvider) -> None:
        assert provider.source in {"znuny", "tiqora"}
        assert isinstance(provider.read_only, bool)

    async def test_get_returns_detail(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        detail = await provider.get(session, seed.a, user_id=seed.user_id)
        assert detail.summary.ref == seed.a
        assert detail.read_only is provider.read_only
        assert detail.summary.name

    async def test_ref_source_mismatch_not_found(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        foreign = CiRef(_other(provider.source), seed.a.id)
        with pytest.raises(CiNotFound):
            await provider.get(session, foreign, user_id=seed.user_id)

    async def test_get_unreadable_is_not_found(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        with pytest.raises(CiNotFound):
            await provider.get(session, seed.hidden, user_id=seed.restricted_user_id)

    async def test_get_unknown_id_not_found(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        with pytest.raises(CiNotFound):
            await provider.get(session, CiRef(provider.source, 2_000_000_000), user_id=seed.user_id)

    # -- search -----------------------------------------------------------------------------

    async def test_search_total_exact(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        page = await provider.search(
            session, CiQuery(text=seed.common_text, limit=1), user_id=seed.user_id
        )
        assert len(page.items) == 1
        assert page.total == 3

    async def test_search_hides_unreadable_from_items_and_total(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        page = await provider.search(
            session, CiQuery(text=seed.common_text), user_id=seed.restricted_user_id
        )
        assert {i.ref for i in page.items} == {seed.a, seed.b}
        assert page.total == 2

    async def test_search_limit_clamped(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        page = await provider.search(
            session, CiQuery(text=seed.common_text, limit=10_000), user_id=seed.user_id
        )
        assert isinstance(page, CiPage)
        assert len(page.items) <= 100
        assert page.total >= 3

    async def test_search_offset_pages_do_not_overlap(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        first = await provider.search(
            session, CiQuery(text=seed.common_text, limit=2, offset=0), user_id=seed.user_id
        )
        second = await provider.search(
            session, CiQuery(text=seed.common_text, limit=2, offset=2), user_id=seed.user_id
        )
        refs = [i.ref for i in (*first.items, *second.items)]
        assert len(refs) == 3 and len(set(refs)) == 3
        assert first.total == second.total == 3

    async def test_search_by_refs(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        page = await provider.search(session, CiQuery(refs=(seed.b, seed.a)), user_id=seed.user_id)
        assert {i.ref for i in page.items} == {seed.a, seed.b}

    # -- get_many ---------------------------------------------------------------------------

    async def test_get_many_omits_unreadable(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        got = await provider.get_many(
            session, [seed.a, seed.hidden, seed.b], user_id=seed.restricted_user_id
        )
        assert set(got) == {seed.a, seed.b}

    async def test_get_many_omits_other_source_and_unknown(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        refs = [
            seed.a,
            CiRef(_other(provider.source), seed.a.id),
            CiRef(provider.source, 2_000_000_000),
        ]
        got = await provider.get_many(session, refs, user_id=seed.user_id)
        assert set(got) == {seed.a}

    # -- versions ---------------------------------------------------------------------------

    async def test_versions_paging(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        page1, total = await provider.versions(
            session, seed.a, user_id=seed.user_id, limit=2, offset=0
        )
        page2, total2 = await provider.versions(
            session, seed.a, user_id=seed.user_id, limit=2, offset=2
        )
        assert total == total2 == seed.a_version_count
        assert len(page1) == 2
        ids = [v.id for v in (*page1, *page2)]
        assert len(ids) == len(set(ids)) == min(seed.a_version_count, 4)

    # -- ticket links -----------------------------------------------------------------------

    async def _link_type(self, provider: CiProvider, session: Any) -> str:
        options = await provider.ticket_link_types(session)
        assert options, "provider offers no ticket<->CI link type"
        return options[0].name

    async def test_link_ticket_idempotent(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        link_type = await self._link_type(provider, session)
        kwargs: dict[str, Any] = {
            "ticket_id": seed.ticket_id,
            "ref": seed.a,
            "link_type": link_type,
            "user_id": seed.user_id,
        }
        assert await provider.link_ticket(session, **kwargs) is True
        assert await provider.link_ticket(session, **kwargs) is False
        links = await provider.ticket_links(session, [seed.ticket_id], user_id=seed.user_id)
        assert [(link.ci.ref, link.link_type) for link in links[seed.ticket_id]] == [
            (seed.a, link_type)
        ]

    async def test_link_ticket_to_unreadable_ci_fails(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        link_type = await self._link_type(provider, session)
        with pytest.raises(CiError):
            await provider.link_ticket(
                session,
                ticket_id=seed.ticket_id,
                ref=seed.hidden,
                link_type=link_type,
                user_id=seed.restricted_user_id,
            )

    async def test_link_ticket_other_source_not_found(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        link_type = await self._link_type(provider, session)
        with pytest.raises(CiNotFound):
            await provider.link_ticket(
                session,
                ticket_id=seed.ticket_id,
                ref=CiRef(_other(provider.source), seed.a.id),
                link_type=link_type,
                user_id=seed.user_id,
            )

    async def test_unlink_counts(self, provider: CiProvider, session: Any, seed: SeededCis) -> None:
        link_type = await self._link_type(provider, session)
        await provider.link_ticket(
            session,
            ticket_id=seed.ticket_id,
            ref=seed.b,
            link_type=link_type,
            user_id=seed.user_id,
        )
        assert await provider.ci_ticket_ids(session, seed.b, user_id=seed.user_id) == [
            seed.ticket_id
        ]
        removed = await provider.unlink_ticket(
            session, ticket_id=seed.ticket_id, ref=seed.b, link_type=None, user_id=seed.user_id
        )
        assert removed == 1
        again = await provider.unlink_ticket(
            session, ticket_id=seed.ticket_id, ref=seed.b, link_type=None, user_id=seed.user_id
        )
        assert again == 0
        assert await provider.ci_ticket_ids(session, seed.b, user_id=seed.user_id) == []

    async def test_ticket_links_empty_for_unlinked_ticket(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        links = await provider.ticket_links(session, [seed.ticket_id], user_id=seed.user_id)
        assert links.get(seed.ticket_id, []) == []

    # -- services ---------------------------------------------------------------------------

    async def test_service_incident_state_worst_wins(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        states = await provider.service_incident_states(session, [seed.service_id])
        state = states[seed.service_id]
        assert state.state is not None
        assert state.state.functionality == "incident"
        assert state.cis_incident == 1

    async def test_service_incident_state_none_without_cis(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        states = await provider.service_incident_states(session, [seed.empty_service_id])
        assert states[seed.empty_service_id].state is None

    async def test_service_cis(self, provider: CiProvider, session: Any, seed: SeededCis) -> None:
        cis = await provider.service_cis(session, seed.service_id, user_id=seed.user_id)
        assert {c.ref for c in cis} == {seed.a, seed.b}
        assert (
            await provider.service_cis(session, seed.empty_service_id, user_id=seed.user_id) == []
        )

    # -- catalog / classes / emptiness ------------------------------------------------------

    async def test_catalogs_and_classes(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        assert await provider.deployment_states(session)
        incident = await provider.incident_states(session)
        assert {"operational", "incident"} <= {s.functionality for s in incident}
        classes = await provider.classes(session, user_id=seed.user_id)
        assert classes and all(c.can_read for c in classes)

    async def test_is_empty_false_after_seed(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        assert await provider.is_empty(session) is False

    # -- transaction discipline -------------------------------------------------------------

    async def test_no_commit_inside_provider(
        self, provider: CiProvider, session: Any, seed: SeededCis
    ) -> None:
        guarded = _NoCommitSession(session)
        link_type = await self._link_type(provider, guarded)
        await provider.search(guarded, CiQuery(text=seed.common_text), user_id=seed.user_id)
        await provider.get(guarded, seed.a, user_id=seed.user_id)
        await provider.link_ticket(
            guarded,
            ticket_id=seed.ticket_id,
            ref=seed.a,
            link_type=link_type,
            user_id=seed.user_id,
        )
        await provider.unlink_ticket(
            guarded, ticket_id=seed.ticket_id, ref=seed.a, link_type=None, user_id=seed.user_id
        )
        await provider.service_incident_states(guarded, [seed.service_id])


__all__ = ["CiProviderContract", "SeededCis"]
