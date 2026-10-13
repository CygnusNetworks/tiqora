"""CI provider registry lookups and the conformance kit run against the in-memory fake."""

from __future__ import annotations

from typing import Any

import pytest

from tests._itsm_fake_provider import FakeCiProvider, FakeSession
from tests.itsm_provider_contract import CiProviderContract, SeededCis
from tiqora.extensions import Registry, set_registry_for_tests
from tiqora.itsm.ci_provider import CiProvider, CiRef
from tiqora.itsm.providers import (
    CI_PROVIDERS,
    CiProviderFactory,
    get_ci_provider,
    tiqora_cmdb_empty,
)
from tiqora.znuny.sysconfig import SysConfig


def _install(*factories: CiProviderFactory) -> None:
    registry = Registry()
    for factory in factories:
        registry.add(CI_PROVIDERS, factory)
    registry.freeze()
    set_registry_for_tests(registry)


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    yield
    set_registry_for_tests(None)


def test_ci_ref_parse_and_str() -> None:
    assert str(CiRef.parse("znuny:12")) == "znuny:12"
    assert CiRef.parse("tiqora:7") == CiRef("tiqora", 7)
    for bad in ("x:1", "znuny:-1", "znuny:"):
        with pytest.raises(ValueError):
            CiRef.parse(bad)


async def test_get_ci_provider_none_when_off() -> None:
    fake, _ = FakeCiProvider.seeded()
    _install(CiProviderFactory(mode="znuny_readthrough", create=lambda _s: fake))
    assert get_ci_provider("off", SysConfig(FakeSession())) is None  # type: ignore[arg-type]


async def test_get_ci_provider_none_without_factory() -> None:
    _install()  # mode tiqora, but no CMDB registered
    assert get_ci_provider("tiqora", SysConfig(FakeSession())) is None  # type: ignore[arg-type]


async def test_get_ci_provider_uses_registered_factory() -> None:
    fake, _ = FakeCiProvider.seeded()
    _install(CiProviderFactory(mode="znuny_readthrough", create=lambda _s: fake))
    sysconfig = SysConfig(FakeSession())  # type: ignore[arg-type]
    got = get_ci_provider("znuny_readthrough", sysconfig)
    assert got is fake
    assert isinstance(got, CiProvider)
    # the other mode has no factory
    assert get_ci_provider("tiqora", sysconfig) is None


async def test_tiqora_cmdb_empty_without_provider_is_true() -> None:
    _install()
    assert await tiqora_cmdb_empty(FakeSession()) is True  # type: ignore[arg-type]


async def test_tiqora_cmdb_empty_follows_the_provider() -> None:
    fake, _ = FakeCiProvider.seeded("tiqora")
    _install(CiProviderFactory(mode="tiqora", create=lambda _s: fake))
    assert await tiqora_cmdb_empty(FakeSession()) is False  # type: ignore[arg-type]
    _install(CiProviderFactory(mode="tiqora", create=lambda _s: FakeCiProvider("tiqora")))
    assert await tiqora_cmdb_empty(FakeSession()) is True  # type: ignore[arg-type]


class TestFakeProviderContract(CiProviderContract):
    @pytest.fixture
    def _built(self) -> tuple[FakeCiProvider, SeededCis]:
        return FakeCiProvider.seeded()

    @pytest.fixture
    def provider(self, _built: tuple[FakeCiProvider, SeededCis]) -> CiProvider:
        return _built[0]

    @pytest.fixture
    def session(self) -> Any:
        return FakeSession()

    @pytest.fixture
    async def seed(self, _built: tuple[FakeCiProvider, SeededCis], session: Any) -> SeededCis:
        return _built[1]


class TestFakeProviderContractTiqoraSource(TestFakeProviderContract):
    """The kit is source-agnostic: the same fake under the Mode B source passes too."""

    @pytest.fixture
    def _built(self) -> tuple[FakeCiProvider, SeededCis]:
        return FakeCiProvider.seeded("tiqora")
