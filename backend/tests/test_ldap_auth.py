"""Unit tests for LDAP/AD bind-search-bind auth (agent + customer portal) and D14 entries.

Uses ldap3's own in-memory ``MOCK_SYNC`` client strategy — a real ``ldap3``
directory simulation, not a hand-rolled fake — so the bind-search-bind
sequence (first bind, user search, optional group searches, re-bind as the
resolved user DN) is exercised against real ldap3 semantics. ``ldap3`` calls
are indirected through ``_import_ldap3`` (see ``domain/_ldap_core.py``) so we
can substitute a thin shim that forces ``client_strategy=MOCK_SYNC`` while
passing everything else straight through to the real module.

The former ``LdapAuthService`` / ``CustomerLdapAuthService`` wrappers are gone (Task 19:
password login runs through ``identity.ldap_login``); the env tests below build the same
connection config from ``TIQORA_LDAP_*`` / ``TIQORA_CUSTOMER_LDAP_*`` via
``env_ldap_config`` and assert the same results as before.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from structlog.testing import capture_logs

from tests.ldap_directory import BIND_DN, BIND_PW, Directory, install_directory
from tiqora.config import Settings
from tiqora.domain import _ldap_core
from tiqora.domain._ldap_core import LdapConfig, LdapEntry, ldap_authenticate_entry
from tiqora.identity.config_models import JitDefaults, LdapProviderConfig, RoleRule
from tiqora.identity.env_providers import env_ldap_config
from tiqora.identity.ldap_login import (
    identity_from_entry,
    ldap_core_config,
    ldap_password_login,
    requested_attributes,
    rule_group_dns,
)
from tiqora.identity.presets import get_preset
from tiqora.identity.store import ProviderRecord

BASE_DN = "dc=example,dc=com"
GROUP_DN = "cn=helpdesk,ou=groups,dc=example,dc=com"
ORG = "dc=example,dc=org"
SUPPORT_DN = "cn=support,ou=groups,dc=example,dc=org"
SALES_DN = "cn=sales,ou=groups,dc=example,dc=org"
ANNA_DN = "uid=anna,ou=people,dc=example,dc=org"


@pytest.fixture
def mock_ldap3(monkeypatch: pytest.MonkeyPatch) -> Iterator[Directory]:
    """One MOCK_SYNC directory reachable as host ``mock-directory``."""
    directory = install_directory(monkeypatch)
    srv = directory.server("mock-directory")
    directory.add(
        srv,
        "uid=alice,ou=people,dc=example,dc=com",
        uid="alice",
        userPassword="alice-secret",
        sn="Alice",
        objectClass="inetOrgPerson",
    )
    directory.add(srv, GROUP_DN, cn="helpdesk", objectClass="groupOfNames", memberUid="alice")
    yield directory


@pytest.fixture
def anna_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Directory]:
    """The brief's directory: anna with mail/givenName/sn/entryUUID, group support."""
    directory = install_directory(monkeypatch)
    srv = directory.server("dir.example.org")
    directory.add(
        srv,
        ANNA_DN,
        uid="anna",
        userPassword="anna-secret",
        mail="anna@example.test",
        givenName="Anna",
        sn="Muster",
        entryUUID="0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
        objectGUID=b"\x01\x02\xfe\xff",
        objectClass="inetOrgPerson",
    )
    directory.add(srv, SUPPORT_DN, cn="support", objectClass="posixGroup", memberUid="anna")
    directory.add(srv, SALES_DN, cn="sales", objectClass="posixGroup", memberUid="bert")
    yield directory


def _env_config(settings: Settings, user_kind: str = "agent") -> LdapConfig:
    return ldap_core_config(env_ldap_config(settings, user_kind))  # type: ignore[arg-type]


async def _env_auth(settings: Settings, login: str, password: str, kind: str = "agent") -> Any:
    entry = await ldap_authenticate_entry(_env_config(settings, kind), login, password)
    return entry.uid if entry else None


def _settings(**overrides: Any) -> Settings:
    base = dict(
        ldap_enabled=True,
        ldap_host="mock-directory",
        ldap_base_dn=BASE_DN,
        ldap_bind_dn=BIND_DN,
        ldap_bind_password=BIND_PW,
        ldap_uid_attr="uid",
    )
    base.update(overrides)
    return Settings(**base)


def _anna_config(**kw: Any) -> LdapConfig:
    base: dict[str, Any] = dict(
        host="dir.example.org",
        base_dn=ORG,
        bind_dn=BIND_DN,
        bind_password=BIND_PW,
        uid_attr="uid",
        access_attr="memberUid",
        user_attr="UID",
    )
    base.update(kw)
    return LdapConfig(**base)


def _record(config: LdapProviderConfig, rules: tuple[RoleRule, ...] = ()) -> ProviderRecord:
    pre = get_preset("ldap")
    return ProviderRecord(
        id=7,
        slug="idpldap-unit",
        kind="ldap",
        source="db",
        name="Verzeichnis",
        audience="agent",
        enabled=True,
        sort=0,
        button_label=None,
        button_icon=None,
        preset="ldap",
        config=config,
        secrets_set=frozenset(),
        match_by="login",
        require_sso_opt_in=False,
        attribute_map=pre.attribute_map,
        jit_enabled=False,
        jit_defaults=JitDefaults(),
        role_rules=rules,
        sync_mode="managed",
        metadata_xml=None,
        metadata_fetched_at=None,
        metadata_error=None,
    )


# --------------------------------------------------------------------------- env (unchanged)


@pytest.mark.asyncio
async def test_bind_search_bind_success_returns_uid(mock_ldap3: Directory) -> None:
    assert await _env_auth(_settings(), "alice", "alice-secret") == "alice"


@pytest.mark.asyncio
async def test_bind_search_bind_wrong_password_rejected(mock_ldap3: Directory) -> None:
    assert await _env_auth(_settings(), "alice", "not-the-password") is None


@pytest.mark.asyncio
async def test_unknown_user_rejected(mock_ldap3: Directory) -> None:
    assert await _env_auth(_settings(), "nosuchuser", "whatever") is None


@pytest.mark.asyncio
async def test_group_mapping_allows_member(mock_ldap3: Directory) -> None:
    settings = _settings(ldap_group_dn=GROUP_DN, ldap_access_attr="memberUid", ldap_user_attr="uid")
    assert await _env_auth(settings, "alice", "alice-secret") == "alice"


@pytest.mark.asyncio
async def test_group_mapping_rejects_non_member(mock_ldap3: Directory) -> None:
    directory = mock_ldap3
    directory.add(
        directory.servers["mock-directory"],
        "uid=bob,ou=people,dc=example,dc=com",
        uid="bob",
        userPassword="bob-secret",
        sn="Bob",
        objectClass="inetOrgPerson",
    )
    settings = _settings(ldap_group_dn=GROUP_DN, ldap_access_attr="memberUid", ldap_user_attr="uid")
    # bob exists and knows his password, but is not in the access group.
    assert await _env_auth(settings, "bob", "bob-secret") is None
    assert await _env_auth(_settings(), "bob", "bob-secret") == "bob"


@pytest.mark.asyncio
async def test_customer_ldap_bind_search_bind_success(mock_ldap3: Directory) -> None:
    settings = Settings(
        customer_ldap_enabled=True,
        customer_ldap_host="mock-directory",
        customer_ldap_base_dn=BASE_DN,
        customer_ldap_bind_dn=BIND_DN,
        customer_ldap_bind_password=BIND_PW,
        customer_ldap_uid_attr="uid",
    )
    assert await _env_auth(settings, "alice", "alice-secret", "customer") == "alice"


@pytest.mark.asyncio
async def test_customer_ldap_unknown_user_rejected(mock_ldap3: Directory) -> None:
    settings = Settings(
        customer_ldap_enabled=True,
        customer_ldap_host="mock-directory",
        customer_ldap_base_dn=BASE_DN,
        customer_ldap_bind_dn=BIND_DN,
        customer_ldap_bind_password=BIND_PW,
        customer_ldap_uid_attr="uid",
    )
    assert await _env_auth(settings, "ghost", "whatever", "customer") is None


@pytest.mark.asyncio
async def test_empty_credentials_rejected_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network() -> Any:
        raise AssertionError("ldap3 must not be touched for empty credentials")

    monkeypatch.setattr(_ldap_core, "_import_ldap3", no_network)
    cfg = _env_config(_settings())
    assert await ldap_authenticate_entry(cfg, "", "whatever") is None
    assert await ldap_authenticate_entry(cfg, "   ", "whatever") is None
    assert await ldap_authenticate_entry(cfg, "alice", "") is None
    assert await _ldap_core.ldap_authenticate(cfg, "alice", "") is None
    # The provider chain rejects before it even reads the provider list.
    found = await ldap_password_login(
        None,  # type: ignore[arg-type]
        _settings(),
        login="alice",
        password="",
        user_kind="agent",
    )
    assert found is None


# --------------------------------------------------------------------------- security


@pytest.mark.asyncio
@pytest.mark.parametrize("login", ["*", "al*", "alice)(uid=*", "*)(|(uid=*", "alice\\", "\x00"])
async def test_filter_values_are_rfc4515_escaped(mock_ldap3: Directory, login: str) -> None:
    # Wildcards/filter syntax in the login must not match alice (or anything).
    assert await _env_auth(_settings(), login, "alice-secret") is None
    searched = [f for f in mock_ldap3.filters if f.startswith("(uid=")]
    assert searched, "the user search ran"
    assert all("*" not in f[len("(uid=") : -1] for f in searched)


@pytest.mark.asyncio
async def test_starttls_negotiated_before_any_bind(mock_ldap3: Directory) -> None:
    settings = _settings(ldap_use_starttls=True)
    assert await _env_auth(settings, "alice", "alice-secret") == "alice"
    events = mock_ldap3.events
    assert events.index("start_tls") < events.index("bind")
    assert events.index("start_tls") < events.index("rebind")
    # One search bind; the user's password is checked by a re-bind on the same
    # (TLS-protected) connection, never on a fresh plain one.
    assert events.count("bind") == 1 and events.count("start_tls") == 1


@pytest.mark.asyncio
async def test_failed_starttls_never_binds(mock_ldap3: Directory) -> None:
    mock_ldap3.starttls_ok = False
    settings = _settings(ldap_use_starttls=True)
    assert await _env_auth(settings, "alice", "alice-secret") is None
    assert "bind" not in mock_ldap3.events and "rebind" not in mock_ldap3.events


@pytest.mark.asyncio
async def test_passwords_never_logged(mock_ldap3: Directory) -> None:
    with capture_logs() as logs:
        assert await _env_auth(_settings(), "alice", "wrong-Pw-4711") is None
        bad_bind = _settings(ldap_bind_password="search-Pw-0815")
        assert await _env_auth(bad_bind, "alice", "alice-secret") is None
        assert await _env_auth(_settings(), "alice", "alice-secret") == "alice"
    text = repr(logs)
    for secret in ("wrong-Pw-4711", "search-Pw-0815", "alice-secret", BIND_PW):
        assert secret not in text
    assert "bind_password" not in repr(_env_config(_settings()))  # repr hides it


@pytest.mark.asyncio
async def test_search_account_without_password_binds_anonymously(mock_ldap3: Directory) -> None:
    # Znuny binds with SearchUserDN only when SearchUserPw is set, too.
    assert await _env_auth(_settings(ldap_bind_password=""), "alice", "alice-secret") == "alice"
    assert mock_ldap3.bind_users[0] is None


# --------------------------------------------------------------------------- D14 entries


@pytest.mark.asyncio
async def test_entry_returns_attributes_and_group_membership(anna_dir: Directory) -> None:
    entry = await ldap_authenticate_entry(
        _anna_config(),
        "anna",
        "anna-secret",
        attributes=["mail", "givenName", "sn", "entryUUID", "telephoneNumber"],
        member_of_group_dns=[SUPPORT_DN, SALES_DN],
    )
    assert isinstance(entry, LdapEntry)
    assert entry.uid == "anna"
    assert entry.dn == ANNA_DN
    assert entry.member_group_dns == ("cn=support,ou=groups,dc=example,dc=org",)
    assert entry.attributes["mail"] == ("anna@example.test",)
    assert entry.attributes["givenName"] == ("Anna",)
    assert entry.attributes["sn"] == ("Muster",)
    assert entry.attributes["entryUUID"] == ("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",)
    assert entry.attributes["telephoneNumber"] == ()  # requested, no value: present-empty
    assert entry.group_search_failed is False  # sales: searched fine, no hit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_group",
    [
        "cn=gone,ou=groups,dc=example,dc=org",  # noSuchObject: non-success result
        "not a dn",  # LDAPInvalidDnError raised by ldap3 before the request
    ],
)
async def test_group_search_error_marks_entry_and_still_logs_in(
    anna_dir: Directory, bad_group: str
) -> None:
    entry = await ldap_authenticate_entry(
        _anna_config(),
        "anna",
        "anna-secret",
        member_of_group_dns=[bad_group, SUPPORT_DN],
    )
    assert entry is not None  # one broken rule never denies every login
    assert entry.member_group_dns == (SUPPORT_DN,)
    assert entry.group_search_failed is True
    cfg = LdapProviderConfig(host="dir.example.org")
    ident = identity_from_entry(_record(cfg), entry)
    assert "ldap:group_dn" not in ident.attributes  # claim absent -> sync skipped


@pytest.mark.asyncio
async def test_access_gate_search_error_denies(anna_dir: Directory) -> None:
    cfg = _anna_config(group_dn="cn=gone,ou=groups,dc=example,dc=org")
    assert await ldap_authenticate_entry(cfg, "anna", "anna-secret") is None


@pytest.mark.asyncio
async def test_entry_membership_by_dn(anna_dir: Directory) -> None:
    srv = anna_dir.servers["dir.example.org"]
    anna_dir.add(srv, "cn=ops,ou=groups,dc=example,dc=org", cn="ops", member=ANNA_DN)
    entry = await ldap_authenticate_entry(
        _anna_config(access_attr="member", user_attr="DN"),
        "anna",
        "anna-secret",
        member_of_group_dns=["cn=ops,ou=groups,dc=example,dc=org", SUPPORT_DN],
    )
    assert entry is not None
    assert entry.member_group_dns == ("cn=ops,ou=groups,dc=example,dc=org",)


@pytest.mark.asyncio
async def test_wrong_password_returns_no_entry(anna_dir: Directory) -> None:
    entry = await ldap_authenticate_entry(
        _anna_config(), "anna", "nope", member_of_group_dns=[SUPPORT_DN]
    )
    assert entry is None


@pytest.mark.asyncio
async def test_ldap_subject_prefers_entry_uuid_then_dn(anna_dir: Directory) -> None:
    cfg = LdapProviderConfig(host="dir.example.org", base_dn=ORG, access_attr="memberUid")
    rule = RoleRule(attribute="ldap:group_dn", value=SUPPORT_DN, role_ids=[1])
    rec = _record(cfg, (rule,))
    entry = await ldap_authenticate_entry(
        _anna_config(),
        "anna",
        "anna-secret",
        attributes=requested_attributes(rec),
        member_of_group_dns=rule_group_dns(rec),
    )
    assert entry is not None
    ident = identity_from_entry(rec, entry)
    assert ident.subject == "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    assert ident.attributes["ldap:dn"] == (ANNA_DN,)
    assert ident.attributes["ldap:group_dn"] == (SUPPORT_DN,)
    assert ident.attributes["uid"] == ("anna",)

    # Configured attribute empty -> objectGUID, then entryUUID; the DN only as last resort.
    blank_attr = _record(cfg.model_copy(update={"subject_attr": ""}))
    assert "objectGUID" in requested_attributes(blank_attr)
    assert "entryUUID" in requested_attributes(blank_attr)
    full = await ldap_authenticate_entry(
        _anna_config(), "anna", "anna-secret", attributes=requested_attributes(blank_attr)
    )
    assert full is not None
    assert identity_from_entry(blank_attr, full).subject == "0102feff"
    only_uuid = LdapEntry(
        uid="anna", dn=ANNA_DN, attributes={"entryUUID": ("u-1",)}, member_group_dns=()
    )
    assert identity_from_entry(blank_attr, only_uuid).subject == "u-1"
    custom = _record(cfg.model_copy(update={"subject_attr": "employeeNumber"}))
    neither = LdapEntry(
        uid="anna",
        dn=ANNA_DN,
        attributes={"employeeNumber": (), "objectGUID": (), "entryUUID": ()},
        member_group_dns=(),
    )
    with capture_logs() as logs:
        assert identity_from_entry(custom, neither).subject == ANNA_DN
    assert any(e["event"] == "ldap_subject_dn_fallback" for e in logs)

    # AD objectGUID is binary: hex, never a lossy text decode.
    ad = _record(cfg.model_copy(update={"subject_attr": "objectGUID"}))
    ad_entry = await ldap_authenticate_entry(
        _anna_config(), "anna", "anna-secret", attributes=requested_attributes(ad)
    )
    assert ad_entry is not None
    assert identity_from_entry(ad, ad_entry).subject == "0102feff"


def test_identity_always_carries_group_dn_claim() -> None:
    cfg = LdapProviderConfig(host="dir.example.org")
    entry = LdapEntry(uid="anna", dn=ANNA_DN, attributes={}, member_group_dns=())
    ident = identity_from_entry(_record(cfg), entry)
    assert ident.attributes["ldap:group_dn"] == ()  # present-empty: role sync still runs


def test_requested_attributes_and_rule_group_dns() -> None:
    cfg = LdapProviderConfig(host="dir.example.org", subject_attr="objectGUID")
    rules = (
        RoleRule(attribute="ldap:group_dn", value=f" {SUPPORT_DN} ", role_ids=[1]),
        RoleRule(attribute="ldap:group_dn", value=SUPPORT_DN, role_ids=[2]),
        RoleRule(attribute="memberOf", value=SALES_DN, role_ids=[3]),
    )
    rec = _record(cfg, rules)
    assert requested_attributes(rec) == [
        "uid",
        "mail",
        "givenName",
        "sn",
        "objectGUID",
        "entryUUID",
        "memberOf",
    ]
    assert rule_group_dns(rec) == [SUPPORT_DN]


# --------------------------------------------------------------------------- review fixes


def _self_signed_pem() -> str:
    """A CA certificate generated at test time (never committed key material)."""
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA example.org")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_ca_pem_is_validated() -> None:
    from pydantic import ValidationError

    pem = _self_signed_pem()
    assert LdapProviderConfig(ca_certs_pem=f"  {pem}  ").ca_certs_pem == pem.strip()
    assert LdapProviderConfig().tls_verify is True  # DB providers verify by default
    for bad in ("hello", "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"):
        with pytest.raises(ValidationError) as err:
            LdapProviderConfig(ca_certs_pem=bad)
        assert "CA-Zertifikat" in str(err.value)


@pytest.mark.asyncio
async def test_tls_verification_settings_reach_ldap3(anna_dir: Directory) -> None:
    import ssl

    pem = _self_signed_pem()
    cfg = ldap_core_config(
        LdapProviderConfig(
            host=" dir.example.org ",  # stripped before ldap3.Server
            base_dn=ORG,
            bind_dn=BIND_DN,
            bind_password=BIND_PW,  # type: ignore[arg-type]
            use_starttls=True,
            ca_certs_pem=pem,
        )
    )
    assert await ldap_authenticate_entry(cfg, "anna", "anna-secret") is not None
    tls = anna_dir.server_kwargs["dir.example.org"]["tls"]
    assert tls.validate == ssl.CERT_REQUIRED  # ldap3 then checks the host name, too
    assert tls.ca_certs_data == pem.strip()
    assert tls.sni == "dir.example.org"


@pytest.mark.asyncio
async def test_env_tls_verify_defaults_off_and_can_be_enabled(mock_ldap3: Directory) -> None:
    import ssl

    from tiqora.identity.env_providers import env_ldap_tls_warnings

    assert await _env_auth(_settings(), "alice", "alice-secret") == "alice"
    assert mock_ldap3.server_kwargs["mock-directory"]["tls"].validate == ssl.CERT_NONE
    assert env_ldap_tls_warnings(_settings()) == ["TIQORA_LDAP_TLS_VERIFY"]
    on = _settings(ldap_tls_verify=True)
    assert await _env_auth(on, "alice", "alice-secret") == "alice"
    assert mock_ldap3.server_kwargs["mock-directory"]["tls"].validate == ssl.CERT_REQUIRED
    assert env_ldap_tls_warnings(on) == []


@pytest.mark.asyncio
async def test_connection_bounds_and_no_referrals(mock_ldap3: Directory) -> None:
    assert await _env_auth(_settings(), "alice", "alice-secret") == "alice"
    assert mock_ldap3.server_kwargs["mock-directory"]["connect_timeout"] == 5
    conn_kwargs = mock_ldap3.connection_kwargs[-1]
    assert conn_kwargs["auto_referrals"] is False
    assert conn_kwargs["receive_timeout"] == 10


@pytest.mark.asyncio
async def test_rebind_refused_when_starttls_not_active(mock_ldap3: Directory) -> None:
    mock_ldap3.starttls_sets_started = False  # start_tls() claims success, TLS is not up
    assert await _env_auth(_settings(ldap_use_starttls=True), "alice", "alice-secret") is None
    assert "rebind" not in mock_ldap3.events


@pytest.mark.asyncio
async def test_ambiguous_user_search_rejected(mock_ldap3: Directory) -> None:
    mock_ldap3.add(
        mock_ldap3.servers["mock-directory"],
        "uid=alice,ou=contractors,dc=example,dc=com",
        uid="alice",
        userPassword="alice-secret",
        sn="Other Alice",
        objectClass="inetOrgPerson",
    )
    with capture_logs() as logs:
        assert await _env_auth(_settings(), "alice", "alice-secret") is None
    assert any(e["event"] == "ldap_user_ambiguous" for e in logs)
    assert "rebind" not in mock_ldap3.events  # no password tried against either entry


@pytest.mark.asyncio
async def test_anonymous_search_with_wrong_password_rejected(mock_ldap3: Directory) -> None:
    settings = _settings(ldap_bind_password="")
    assert await _env_auth(settings, "alice", "wrong") is None
    assert mock_ldap3.bind_users == [None]  # anonymous search bind, then a failed re-bind


@pytest.mark.asyncio
@pytest.mark.parametrize("password", ["alice-secret\x00", "\x00", "alice\x07secret"])
async def test_nul_and_control_char_passwords_rejected(
    mock_ldap3: Directory, password: str
) -> None:
    assert await _env_auth(_settings(), "alice", password) is None
    assert await _env_auth(_settings(), "alice", "alice-secret") == "alice"


@pytest.mark.asyncio
async def test_exact_escaped_filters(mock_ldap3: Directory) -> None:
    srv = mock_ldap3.servers["mock-directory"]
    mock_ldap3.add(
        srv,
        "uid=o*brien,ou=people,dc=example,dc=com",
        uid="o*brien",
        userPassword="ob-secret",
        sn="O'Brien",
        objectClass="inetOrgPerson",
    )
    mock_ldap3.add(srv, "cn=ops,ou=groups,dc=example,dc=com", cn="ops", memberUid="o*brien")
    assert await _env_auth(_settings(), "alice\\", "x") is None
    assert await _env_auth(_settings(), "\x00", "x") is None
    gate = _settings(
        ldap_group_dn="cn=ops,ou=groups,dc=example,dc=com",
        ldap_access_attr="memberUid",
        ldap_user_attr="UID",
    )
    assert await _env_auth(gate, "o*brien", "ob-secret") == "o*brien"
    assert "(uid=alice\\5c)" in mock_ldap3.filters
    assert "(uid=\\00)" in mock_ldap3.filters
    assert "(uid=o\\2abrien)" in mock_ldap3.filters
    assert "(memberUid=o\\2abrien)" in mock_ldap3.filters  # group member filter, UID mode


@pytest.mark.asyncio
@pytest.mark.parametrize("user_attr", ["sAMAccountName", "uid", "dn", "UID"])
async def test_env_user_attr_other_than_dn_compares_the_login(
    mock_ldap3: Directory, user_attr: str
) -> None:
    # Znuny `UserAttr eq 'DN'`: only exactly "DN" means DN; the group lists the login.
    settings = _settings(
        ldap_group_dn=GROUP_DN, ldap_access_attr="memberUid", ldap_user_attr=user_attr
    )
    assert env_ldap_config(settings, "agent").user_attr == "UID"
    assert await _env_auth(settings, "alice", "alice-secret") == "alice"
    assert "(memberUid=alice)" in mock_ldap3.filters


@pytest.mark.asyncio
async def test_env_user_attr_dn_compares_the_dn(mock_ldap3: Directory) -> None:
    settings = _settings(ldap_group_dn=GROUP_DN, ldap_access_attr="member", ldap_user_attr="DN")
    assert env_ldap_config(settings, "agent").user_attr == "DN"
    assert await _env_auth(settings, "alice", "alice-secret") is None  # helpdesk lists memberUid
    assert "(member=uid=alice,ou=people,dc=example,dc=com)" in mock_ldap3.filters
