"""Golden Mode A parity on the Znuny ITSM peers (I15, wave-4 itsm-core, Task 28).

Mode A = Znuny runs the ITSM, Tiqora reads its tables and links tickets to its CIs. Every case
compares Tiqora (``ZnunyCiProvider``, ``link_objects``, ``load_matrix`` ...) with the real
Znuny code of the peer (``Kernel::System::ITSMConfigItem``, ``LinkObject``, ``Service``,
``ITSMCIPAllocate`` ...) on the SAME database.

    GOLDEN_PEER=znuny-6.5-itsm just golden-up && GOLDEN_PEER=znuny-6.5-itsm just golden-seed
    cd backend && GOLDEN=1 GOLDEN_PEER=znuny-6.5-itsm uv run python -m pytest -q -m golden \\
        ../tests/golden/test_itsm_mode_a.py
    # the two plain-peer cases (setup creates the rows Znuny's ITSM packages would create):
    GOLDEN_PEER=znuny-6.5 ... same command

The toolkit runs ONE peer per session, so the two-peer case of the plan ("on an ITSM peer a
no-op, on a plain peer identical to the packages") is split in two tests that are skipped on
the wrong kind of peer (``itsm_peer`` / ``plain_peer``, see ``conftest.py``): the ITSM-peer test
proves the seed definitions equal the rows the packages created, the plain-peer test proves
Tiqora's setup writes exactly those definitions.

Behaviour differences between Tiqora and Znuny found by these tests (no version-specific one so
far: the 6.5.4 and 7.3.5 sources of ``LinkObject/ITSMConfigItem.pm`` are identical, ``diff``):

* ``Kernel/System/LinkObject/ITSMConfigItem.pm::LinkAddPost`` / ``LinkDeletePost`` also recalculate
  ``configitem.cur_inci_state_id`` (``CurInciStateRecalc``) and write a ``LinkAdd`` /
  ``LinkDelete`` row to ``configitem_history`` (``ITSMConfigItem/Event/DoHistory.pm``). Mode A
  never writes an ITSM table: Tiqora does neither (open question 1 of the plan, asserted in
  ``test_configitem_history_difference_documented``). The ticket-side history entries
  (``CILinkAdd`` / ``CILinkDelete``) are written by both.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymysql
import pytest
import yaml
from _helpers import znuny_console, znuny_copy_in, znuny_perl_eval
from peer_env import resolve as resolve_peer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.itsm.ci_provider import CiQuery, CiRef
from tiqora.itsm.seed_definitions import SeedSet, for_profile
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache, detect_znuny_itsm
from tiqora.itsm.znuny_adapter.provider import ZnunyCiProvider
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.golden

GOLDEN_DIR = Path(__file__).resolve().parent
SEED_SCRIPT = GOLDEN_DIR / "itsm_seed.pl"
SEED_REMOTE = "/tmp/itsm_seed.pl"  # noqa: S108 - path inside the throw-away peer container
USER_ID = 1  # root@localhost, member of admin / itsm-configitem

SessionFactory = async_sessionmaker[AsyncSession]

# TiqoraSync (the Znuny add-on) drains ``tiqora_cache_invalidation``; the peer image does not ship
# it. Tiqora's writes are only visible to Znuny's cached reads (LinkObject, Service, ...) after a
# run of it, as in production (daemon cron task), so the module copies it in for the session.
TIQORASYNC_MODULE = Path(
    os.environ.get("TIQORASYNC_MODULE")
    or GOLDEN_DIR.parents[1] / "packages/znuny-addon/TiqoraSync/Kernel/System/TiqoraSync.pm"
)
TIQORASYNC_PEER_PATH = "/opt/otrs/Kernel/System/TiqoraSync.pm"

# --- peer facts ------------------------------------------------------------------------------

#: base peer -> (schema profile, ITSMCore, ITSMConfigurationManagement) as installed by the toolkit
EXPECTED_PEERS: dict[str, tuple[str, str, str]] = {
    "znuny-6.5": ("itsm-6", "6.5.5", "6.5.4"),
    "znuny-7.3": ("itsm-7", "7.3.3", "7.3.5"),
}


@dataclass(frozen=True)
class Peer:
    id: str
    base: str
    profile: str
    core_version: str
    cmdb_version: str


@pytest.fixture(scope="module")
def peer(golden_peer_id: str) -> Peer:
    """The ITSM peer of this session (tests using it carry the ``itsm_peer`` marker)."""
    base = resolve_peer(golden_peer_id)["GOLDEN_BASE_PEER"]
    profile, core, cmdb = EXPECTED_PEERS[base]
    return Peer(golden_peer_id, base, profile, core, cmdb)


# --- Perl side -------------------------------------------------------------------------------

_PRELUDE = r"""
use strict;
use warnings;
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => { LogPrefix => 'golden-itsm-mode-a' },
);
my $JSON         = $Kernel::OM->Get('Kernel::System::JSON');
# undef on a plain peer (no ITSM packages)
my $CIObject     = eval { $Kernel::OM->Get('Kernel::System::ITSMConfigItem') };
my $LinkObject   = $Kernel::OM->Get('Kernel::System::LinkObject');
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $DBObject     = $Kernel::OM->Get('Kernel::System::DB');
my $ConfigObject = $Kernel::OM->Get('Kernel::Config');

sub Emit { print "GOLDEN_JSON_BEGIN\n" . $JSON->Encode( Data => $_[0] ) . "\nGOLDEN_JSON_END\n"; }

# XMLData of one CI version -> { "NIC[1]/IPAddress[1]" => Content }
sub Flatten {
    my ( $Node, $Prefix, $Out ) = @_;
    for my $Key ( sort keys %{$Node} ) {
        next if $Key eq 'TagKey' || $Key eq 'Content';
        my $Instances = $Node->{$Key};
        next if ref $Instances ne 'ARRAY';
        for my $Index ( 1 .. $#{$Instances} ) {
            my $Child = $Instances->[$Index];
            next if ref $Child ne 'HASH';
            my $Path = "$Prefix$Key\[$Index]";
            my $Content = $Child->{Content};
            $Out->{$Path} = $Content if defined $Content && length $Content;
            Flatten( $Child, "$Path/", $Out );
        }
    }
    return;
}

sub VersionFlat {
    my ($Version) = @_;
    my %Flat;
    my $Root = $Version->{XMLData} && $Version->{XMLData}->[1] ? $Version->{XMLData}->[1] : {};
    my $V    = $Root->{Version} ? $Root->{Version}->[1] : {};
    Flatten( $V, '', \%Flat );
    return \%Flat;
}

sub Rows {
    my (%P) = @_;
    $DBObject->Prepare( SQL => $P{SQL}, Bind => $P{Bind} || [] ) or die "SQL failed: $P{SQL}";
    my @Rows;
    while ( my @Row = $DBObject->FetchrowArray() ) {
        push @Rows, [ map { defined $_ ? "$_" : undef } @Row ];
    }
    return \@Rows;
}

sub NewTicket {
    my ($Title) = @_;
    my $ID = $TicketObject->TicketCreate(
        Title => $Title, Queue => 'Golden', Lock => 'unlock', Priority => '3 normal',
        State => 'new', CustomerID => 'golden-customer', CustomerUser => 'golden.customer',
        OwnerID => 1, UserID => 1,
    ) or die 'TicketCreate failed';
    return { id => $ID + 0, number => $TicketObject->TicketNumberLookup( TicketID => $ID ) };
}
"""


def znuny_sync() -> None:
    """One run of the TiqoraSync add-on in the peer (what the daemon cron task does)."""
    znuny_perl_eval(
        "use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);"
        "use Kernel::System::ObjectManager;"
        "local $Kernel::OM = Kernel::System::ObjectManager->new("
        "'Kernel::System::Log' => { LogPrefix => 'golden-itsm-sync' });"
        "$Kernel::OM->Get('Kernel::System::TiqoraSync')->Run() or die 'TiqoraSync->Run failed';"
    )


def znuny_json(body: str) -> Any:
    """Run Perl *body* in the peer (after the prelude) and decode what it ``Emit``ted."""
    out = znuny_perl_eval(_PRELUDE + body)
    match = re.search(r"GOLDEN_JSON_BEGIN\n(.*?)\nGOLDEN_JSON_END", out, re.S)
    assert match, f"no result block in peer output: {out[-2000:]!r}"
    return json.loads(match.group(1))


# --- shared fixtures -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def itsm_seed(golden_peer_id: str) -> dict[str, Any]:
    """The seed of ``itsm_seed.pl`` (idempotent): service, SLA, CIs, links, one ticket."""
    znuny_copy_in(SEED_SCRIPT, SEED_REMOTE)
    out = znuny_perl_eval(f"do '{SEED_REMOTE}' or die $@ || $!;")
    match = re.search(r"GOLDEN_ITSM_SEED_BEGIN\n(.*?)\nGOLDEN_ITSM_SEED_END", out, re.S)
    assert match, f"seed produced no result block: {out!r}"
    seed: dict[str, Any] = json.loads(match.group(1))
    return seed


async def _itsm_loader(session: AsyncSession) -> ItsmSettings:
    return ItsmSettings()


def make_provider(session: AsyncSession) -> ZnunyCiProvider:
    return ZnunyCiProvider(SysConfig(session), settings_loader=_itsm_loader)


@pytest.fixture(scope="module", autouse=True)
def _tiqorasync(golden_pymysql_dsn: dict[str, object]) -> Generator[None, None, None]:
    """The add-on module in the peer, its watermark at the current end of the signal table."""
    znuny_copy_in(TIQORASYNC_MODULE, TIQORASYNC_PEER_PATH)
    conn = pymysql.connect(**golden_pymysql_dsn)  # type: ignore[arg-type]
    key = "tiqorasync.watermark"
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM tiqora_settings WHERE `key` = %s", (key,))
        row = cur.fetchone()
        saved = None if row is None else row[0]
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
        fetched = cur.fetchone()
        start = int(fetched[0]) if fetched else 0
        cur.execute(
            "REPLACE INTO tiqora_settings (`key`, value) VALUES (%s, %s)", (key, str(start))
        )
    try:
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tiqora_cache_invalidation WHERE id > %s", (start,))
            if saved is None:
                cur.execute("DELETE FROM tiqora_settings WHERE `key` = %s", (key,))
            else:
                cur.execute(
                    "REPLACE INTO tiqora_settings (`key`, value) VALUES (%s, %s)", (key, saved)
                )
        conn.close()
        znuny_perl_eval(f"unlink '{TIQORASYNC_PEER_PATH}'; print 'ok';")


@pytest.fixture(autouse=True)
def _fresh_detect_cache() -> None:
    clear_detect_cache()


async def _commit(
    factory: SessionFactory,
    action: Callable[[AsyncSession, ZnunyCiProvider], Awaitable[Any]],
) -> Any:
    """Run *action* in its own session and commit (what an API request does)."""
    async with factory() as session:
        result = await action(session, make_provider(session))
        await session.commit()
        return result


# =================================================================================================
# detect
# =================================================================================================


@pytest.mark.itsm_peer
async def test_detect_profile_on_peer(peer: Peer, golden_session_factory: SessionFactory) -> None:
    """Tiqora's detection equals what the peer's package manager reports."""
    repo = znuny_json(
        r"""
my $Packages = $Kernel::OM->Get('Kernel::System::Package');
Emit({ map { $_->{Name}->{Content} => $_->{Version}->{Content} }
       grep { ( $_->{Status} // '' ) eq 'installed' } $Packages->RepositoryList() });
"""
    )
    assert repo["ITSMCore"] == peer.core_version
    assert repo["ITSMConfigurationManagement"] == peer.cmdb_version

    async with golden_session_factory() as session:
        info = await detect_znuny_itsm(session, use_cache=False)
    assert info.unsupported_reason is None
    assert info.profile == peer.profile
    assert info.core_installed and info.cmdb_installed
    assert info.change_installed  # the package set ships ITSMChangeManagement
    assert dict(info.packages) == repo
    assert info.packages["ITSMCore"] == peer.core_version
    assert info.packages["ITSMConfigurationManagement"] == peer.cmdb_version


# =================================================================================================
# CI search / detail / versions
# =================================================================================================

_ZNUNY_CIS_PERL = r"""
my $All = $CIObject->ConfigItemSearchExtended() || [];
my %By;
for my $ID ( @{$All} ) {
    my $CI = $CIObject->ConfigItemGet( ConfigItemID => $ID, Cache => 0 );
    my $V  = $CIObject->VersionGet( ConfigItemID => $ID, XMLDataGet => 1 );
    $By{$ID} = {
        number => $CI->{Number}, class => $CI->{Class}, class_id => $CI->{ClassID},
        name => $V->{Name}, depl_id => $CI->{CurDeplStateID}, depl => $CI->{CurDeplState},
        depl_type => $CI->{CurDeplStateType}, inci_id => $CI->{CurInciStateID},
        inci => $CI->{CurInciState}, inci_type => $CI->{CurInciStateType},
        flat => VersionFlat($V),
        definition_version =>
            $CIObject->DefinitionGet( DefinitionID => $V->{DefinitionID} )->{Version},
    };
}
Emit({ ids => [ map { $_ + 0 } @{$All} ], by_id => \%By });
"""


def _flatten_attrs(attrs: Any, prefix: str = "") -> dict[str, str]:
    """Tiqora's attribute tree as ``{"NIC[1]/IPAddress[1]": raw}``, same paths as ``Flatten``."""
    counts: dict[str, int] = {}
    out: dict[str, str] = {}
    for attr in attrs:
        counts[attr.key] = counts.get(attr.key, 0) + 1
        path = f"{prefix}{attr.key}[{counts[attr.key]}]"
        if attr.raw not in (None, ""):
            out[path] = attr.raw
        out.update(_flatten_attrs(attr.children, f"{path}/"))
    return out


async def _all_tiqora_cis(provider: ZnunyCiProvider, session: AsyncSession) -> list[Any]:
    items: list[Any] = []
    offset = 0
    while True:
        page = await provider.search(session, CiQuery(limit=100, offset=offset), user_id=USER_ID)
        items.extend(page.items)
        offset += 100
        if offset >= page.total:
            return items


def _znuny_search_ids(args: str) -> list[int]:
    ids: list[int] = znuny_json(
        f"my $IDs = $CIObject->ConfigItemSearchExtended({args}) || []; "
        "Emit([ map { $_ + 0 } @{$IDs} ]);"
    )
    return sorted(ids)


@pytest.mark.itsm_peer
async def test_ci_search_and_detail_equal_znuny(
    peer: Peer, itsm_seed: dict[str, Any], golden_session_factory: SessionFactory
) -> None:
    """ConfigItemSearchExtended / ConfigItemGet / VersionGet vs the adapter, modulo display.

    Compared per CI: number, class, name, current deployment / incident state (id, name and the
    ``Functionality`` type), the attribute values by path (``NIC[1]/IPAddress[1]``) and the
    definition version. Display formatting (labels, resolved catalog names) is not compared, the
    stored value is. Searches: the same id sets as Znuny's own search.
    """
    znuny = znuny_json(_ZNUNY_CIS_PERL)
    assert len(znuny["ids"]) >= 3  # at least the seed

    async with golden_session_factory() as session:
        provider = make_provider(session)
        items = await _all_tiqora_cis(provider, session)
        assert sorted(i.ref.id for i in items) == sorted(znuny["ids"])

        for item in items:
            want = znuny["by_id"][str(item.ref.id)]
            assert item.ref.source == "znuny"
            assert item.number == want["number"]
            assert item.class_name == want["class"]
            assert item.class_key == str(want["class_id"])
            assert item.deployment_state is not None and item.incident_state is not None
            assert item.deployment_state.key == str(want["depl_id"])
            assert item.deployment_state.name == want["depl"]
            assert item.deployment_state.functionality == want["depl_type"]
            assert item.incident_state.key == str(want["inci_id"])
            assert item.incident_state.name == want["inci"]
            assert item.incident_state.functionality == want["inci_type"]

            detail = await provider.get(session, item.ref, user_id=USER_ID)
            assert detail.read_only is True
            assert detail.summary.name == want["name"] == item.name
            assert detail.definition_version == int(want["definition_version"])
            assert _flatten_attrs(detail.attributes) == want["flat"], item.number

        page = await provider.search(session, CiQuery(text="web01", limit=100), user_id=USER_ID)
        assert sorted(i.ref.id for i in page.items) == _znuny_search_ids("Name => '*web01*'")

        page = await provider.search(session, CiQuery(text="1025", limit=100), user_id=USER_ID)
        assert {i.ref.id for i in page.items} >= set(_znuny_search_ids("Number => '1025*'"))

        # attribute search: Znuny's "What" search on a searchable attribute
        page = await provider.search(
            session, CiQuery(text="Example Hardware", limit=100), user_id=USER_ID
        )
        assert sorted(i.ref.id for i in page.items) == _znuny_search_ids(
            """What => [ { "[%]{'Version'}[%]{'Vendor'}[%]{'Content'}" => 'Example Hardware' } ]"""
        )

        # every deployment / incident state against Znuny's ids (the CURRENT state of the CI)
        for kind, perl_arg, states in (
            ("deployment", "DeplStateIDs", await provider.deployment_states(session)),
            ("incident", "InciStateIDs", await provider.incident_states(session)),
        ):
            for state in states:
                page = await provider.search(
                    session,
                    CiQuery(
                        limit=100,
                        deployment_state_keys=(state.key,) if kind == "deployment" else (),
                        incident_state_keys=(state.key,) if kind == "incident" else (),
                    ),
                    user_id=USER_ID,
                )
                assert sorted(i.ref.id for i in page.items) == _znuny_search_ids(
                    f"{perl_arg} => [{state.key}]"
                ), (kind, state.name)


@pytest.mark.itsm_peer
async def test_versions_equal_znuny(
    peer: Peer, itsm_seed: dict[str, Any], golden_session_factory: SessionFactory
) -> None:
    """``VersionList`` / ``VersionGet`` of every seed CI equal ``versions()`` / ``get(version)``."""
    cis = itsm_seed["cis"]
    ids = [cis["web01"]["id"], cis["db01"]["id"], cis["net01"]["id"]]
    znuny = znuny_json(
        "my %Out; for my $ID ("
        + ",".join(str(i) for i in ids)
        + r""") {
    my @List = @{ $CIObject->VersionList( ConfigItemID => $ID ) || [] };
    $Out{$ID} = [ map {
        my $V = $CIObject->VersionGet( VersionID => $_, XMLDataGet => 1 );
        { id => $_ + 0, name => $V->{Name}, depl => $V->{DeplState}, depl_id => $V->{DeplStateID},
          inci => $V->{InciState}, inci_id => $V->{InciStateID}, create_by => $V->{CreateBy},
          created => $V->{CreateTime}, flat => VersionFlat($V) }
    } @List ];
}
Emit(\%Out);"""
    )
    assert len(znuny[str(cis["db01"]["id"])]) >= 3  # seed: db01 has three versions

    async with golden_session_factory() as session:
        provider = make_provider(session)
        for ci_id in ids:
            ref = CiRef("znuny", ci_id)
            versions, total = await provider.versions(
                session, ref, user_id=USER_ID, limit=100, offset=0
            )
            expected = znuny[str(ci_id)]  # VersionList order = creation order (oldest first)
            assert total == len(expected)
            oldest_first = list(reversed(versions))
            assert [v.id for v in oldest_first] == [e["id"] for e in expected]
            assert [v.seq for v in oldest_first] == list(range(1, len(expected) + 1))
            for version, want in zip(oldest_first, expected, strict=True):
                assert version.name == want["name"]
                assert version.deployment_state is not None
                assert version.deployment_state.name == want["depl"]
                assert version.deployment_state.key == str(want["depl_id"])
                assert version.incident_state is not None
                assert version.incident_state.name == want["inci"]
                assert version.incident_state.key == str(want["inci_id"])
                assert version.created_by == int(want["create_by"])
                assert version.created_at.strftime("%Y-%m-%d %H:%M:%S") == want["created"]
                detail = await provider.get(session, ref, user_id=USER_ID, version_id=version.id)
                assert detail.version is not None and detail.version.id == version.id
                assert _flatten_attrs(detail.attributes) == want["flat"], (ci_id, version.id)


# =================================================================================================
# ticket <-> CI links
# =================================================================================================


@pytest.mark.itsm_peer
async def test_link_from_tiqora_visible_in_znuny(
    peer: Peer, itsm_seed: dict[str, Any], golden_session_factory: SessionFactory
) -> None:
    """A ticket-to-CI link made in Tiqora shows in Znuny's ticket links and the CI zoom."""
    ci = itsm_seed["cis"]["db01"]
    ticket = znuny_json("Emit(NewTicket('itsm28 tiqora to znuny'));")
    ref = CiRef("znuny", ci["id"])
    try:
        created = await _commit(
            golden_session_factory,
            lambda s, p: p.link_ticket(
                s, ticket_id=ticket["id"], ref=ref, link_type="RelevantTo", user_id=USER_ID
            ),
        )
        assert created is True
        znuny_sync()  # the daemon's TiqoraSync run: drops Znuny's cached LinkObject lists
        seen = znuny_json(
            f"""
my $TicketList = $LinkObject->LinkList( Object => 'Ticket', Key => {ticket["id"]},
    Object2 => 'ITSMConfigItem', State => 'Valid', UserID => 1 ) || {{}};
my $CIList = $LinkObject->LinkList( Object => 'ITSMConfigItem', Key => {ci["id"]},
    Object2 => 'Ticket', State => 'Valid', UserID => 1 ) || {{}};
my @Hist = map {{ $_->{{HistoryType}} . '|' . $_->{{Name}} }}
    grep {{ $_->{{HistoryType}} =~ /^CILink/ }}
    $TicketObject->HistoryGet( TicketID => {ticket["id"]}, UserID => 1 );
Emit({{
    ticket_side => $TicketList->{{ITSMConfigItem}} || {{}},
    ci_side     => $CIList->{{Ticket}} || {{}},
    history     => \\@Hist,
}});
"""
        )
        # ticket zoom and CI zoom both list the other end (RelevantTo is an unpointed type, so
        # Znuny reports every end as "Source")
        assert str(ci["id"]) in seen["ticket_side"]["RelevantTo"]["Source"]
        assert str(ticket["id"]) in seen["ci_side"]["RelevantTo"]["Source"]
        assert seen["history"] == [f"CILinkAdd|%%{ci['id']}%%RelevantTo"]

        # Tiqora reads it back, from the ticket and from the CI
        async with golden_session_factory() as session:
            provider = make_provider(session)
            links = await provider.ticket_links(session, [ticket["id"]], user_id=USER_ID)
            assert [(lk.ci.ref, lk.link_type) for lk in links[ticket["id"]]] == [
                (ref, "RelevantTo")
            ]
            assert ticket["id"] in await provider.ci_ticket_ids(session, ref, user_id=USER_ID)
    finally:
        removed = await _commit(
            golden_session_factory,
            lambda s, p: p.unlink_ticket(
                s, ticket_id=ticket["id"], ref=ref, link_type=None, user_id=USER_ID
            ),
        )
    assert removed == 1
    znuny_sync()
    gone = znuny_json(
        f"Emit($LinkObject->LinkList(Object => 'Ticket', Key => {ticket['id']},"
        " Object2 => 'ITSMConfigItem', State => 'Valid', UserID => 1) || {});"
    )
    assert gone == {}


@pytest.mark.itsm_peer
async def test_link_from_znuny_visible_in_tiqora(
    peer: Peer, itsm_seed: dict[str, Any], golden_session_factory: SessionFactory
) -> None:
    """Links Znuny makes (CI as source as in the CI zoom, ticket as source) show up in Tiqora."""
    web = itsm_seed["cis"]["web01"]
    net = itsm_seed["cis"]["net01"]
    ticket = znuny_json("Emit(NewTicket('itsm28 znuny to tiqora'));")
    znuny_json(
        f"""
$LinkObject->LinkAdd( SourceObject => 'ITSMConfigItem', SourceKey => {web["id"]},
    TargetObject => 'Ticket', TargetKey => {ticket["id"]}, Type => 'RelevantTo',
    State => 'Valid', UserID => 1 ) or die 'LinkAdd (CI as source) failed';
$LinkObject->LinkAdd( SourceObject => 'Ticket', SourceKey => {ticket["id"]},
    TargetObject => 'ITSMConfigItem', TargetKey => {net["id"]}, Type => 'RelevantTo',
    State => 'Valid', UserID => 1 ) or die 'LinkAdd (ticket as source) failed';
Emit({{ok => 1}});
"""
    )
    try:
        async with golden_session_factory() as session:
            provider = make_provider(session)
            links = await provider.ticket_links(session, [ticket["id"]], user_id=USER_ID)
            found = {(lk.ci.ref.id, lk.link_type) for lk in links[ticket["id"]]}
            assert found == {(web["id"], "RelevantTo"), (net["id"], "RelevantTo")}
            for ci in (web, net):
                ids = await provider.ci_ticket_ids(
                    session, CiRef("znuny", ci["id"]), user_id=USER_ID
                )
                assert ticket["id"] in ids
            # linking again, from the other side, is no second link (an unpointed type)
            again = await provider.link_ticket(
                session,
                ticket_id=ticket["id"],
                ref=CiRef("znuny", web["id"]),
                link_type="RelevantTo",
                user_id=USER_ID,
            )
            assert again is False
            await session.rollback()
    finally:
        znuny_json(
            f"""
for my $CIID ({web["id"]}, {net["id"]}) {{
    $LinkObject->LinkDelete( Object1 => 'Ticket', Key1 => {ticket["id"]},
        Object2 => 'ITSMConfigItem', Key2 => $CIID, Type => 'RelevantTo', UserID => 1 );
}}
Emit({{ok => 1}});
"""
        )


# =================================================================================================
# link parity: Znuny LinkAdd / LinkDelete vs Tiqora, rows and ticket history
# =================================================================================================

_LINK_HISTORY_TYPES = ("TicketLinkAdd", "TicketLinkDelete", "CILinkAdd", "CILinkDelete")


def _link_rows(conn: pymysql.Connection, ticket_ids: list[int]) -> list[tuple[Any, ...]]:
    marks = ",".join(["%s"] * len(ticket_ids))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o1.name, lr.source_key, o2.name, lr.target_key, lt.name, ls.name, lr.create_by"
            " FROM link_relation lr"
            " JOIN link_object o1 ON o1.id = lr.source_object_id"
            " JOIN link_object o2 ON o2.id = lr.target_object_id"
            " JOIN link_type lt ON lt.id = lr.type_id"
            " JOIN link_state ls ON ls.id = lr.state_id"
            f" WHERE (o1.name = 'Ticket' AND lr.source_key IN ({marks}))"  # noqa: S608
            f" OR (o2.name = 'Ticket' AND lr.target_key IN ({marks}))",
            [str(i) for i in ticket_ids] * 2,
        )
        return list(cur.fetchall())


def _history_rows(conn: pymysql.Connection, ticket_id: int) -> list[tuple[Any, ...]]:
    marks = ",".join(["%s"] * len(_LINK_HISTORY_TYPES))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ht.name, th.name, th.owner_id, th.queue_id, th.state_id, th.priority_id,"
            " th.type_id, th.create_by, th.change_by, th.article_id"
            " FROM ticket_history th JOIN ticket_history_type ht ON ht.id = th.history_type_id"
            f" WHERE th.ticket_id = %s AND ht.name IN ({marks}) ORDER BY th.id",  # noqa: S608
            [ticket_id, *_LINK_HISTORY_TYPES],
        )
        return list(cur.fetchall())


def _roles(names: dict[str, dict[str, Any]]) -> dict[str, str]:
    """``{"123": "<A>", "2026...": "<A:tn>"}`` for every twin ticket (id and ticket number)."""
    out: dict[str, str] = {}
    for role, ticket in names.items():
        out[str(ticket["id"])] = f"<{role}>"
        out[str(ticket["number"])] = f"<{role}:tn>"
    return out


def _norm_value(value: Any, roles: dict[str, str]) -> Any:
    if isinstance(value, str):
        return re.sub(r"\d+", lambda m: roles.get(m.group(0), m.group(0)), value)
    return value


def _norm_link(row: tuple[Any, ...], roles: dict[str, str]) -> tuple[Any, ...]:
    """Object names, types, state; ticket keys masked by role, the CI key masked as ``<CI>``."""
    src_obj, src_key, tgt_obj, tgt_key, *rest = row
    mask = {
        "ITSMConfigItem": lambda key: "<CI>",
        "Ticket": lambda key: roles.get(str(key), str(key)),
    }
    return (src_obj, mask[src_obj](src_key), tgt_obj, mask[tgt_obj](tgt_key), *rest)


def _norm_history(row: tuple[Any, ...], roles: dict[str, str]) -> tuple[Any, ...]:
    """Twin ids and numbers masked; ``CILink*`` names (``%%<ci id>%%<type>``) keep ``<CI>``."""
    history_type, name, *rest = row
    if str(history_type).startswith("CILink"):
        name = re.sub(r"^%%\d+%%", "%%<CI>%%", str(name))
    else:
        name = _norm_value(name, roles)
    return (history_type, name, *rest)


def _link_state(
    conn: pymysql.Connection, tickets: dict[str, dict[str, Any]]
) -> dict[str, list[tuple[Any, ...]]]:
    roles = _roles(tickets)
    links = [_norm_link(r, roles) for r in _link_rows(conn, [t["id"] for t in tickets.values()])]
    state: dict[str, list[tuple[Any, ...]]] = {
        "links": sorted(links, key=lambda r: tuple(str(v) for v in r))
    }
    for role, ticket in tickets.items():
        state[f"history_{role}"] = [
            _norm_history(r, roles) for r in _history_rows(conn, ticket["id"])
        ]
    return state


@pytest.mark.itsm_peer
async def test_link_parity_both_directions(
    peer: Peer,
    itsm_seed: dict[str, Any],
    golden_session_factory: SessionFactory,
    golden_conn: pymysql.Connection,
) -> None:
    """Twin tickets, one pair linked and unlinked by Znuny, one by Tiqora: the same rows.

    Ticket-to-ticket (``ParentChild``) and ticket-to-CI (``RelevantTo``), after the add and after
    the delete: ``link_relation`` rows (object, type and state by name, creator) and the ticket
    history entries ``TicketLinkAdd`` / ``TicketLinkDelete`` / ``CILinkAdd`` / ``CILinkDelete``
    with every column of the history row, after masking the twin ids and numbers.
    """
    from tiqora.domain.ticket_write_service import link_objects, unlink_objects

    ci = itsm_seed["cis"]["web01"]
    ref = CiRef("znuny", ci["id"])
    z = {"A": znuny_json("Emit(NewTicket('itsm28 parity z-a'));")}
    z["B"] = znuny_json("Emit(NewTicket('itsm28 parity z-b'));")
    t = {"A": znuny_json("Emit(NewTicket('itsm28 parity t-a'));")}
    t["B"] = znuny_json("Emit(NewTicket('itsm28 parity t-b'));")

    try:
        # --- add -----------------------------------------------------------------------------
        znuny_json(
            f"""
$LinkObject->LinkAdd( SourceObject => 'Ticket', SourceKey => {z["A"]["id"]},
    TargetObject => 'Ticket', TargetKey => {z["B"]["id"]}, Type => 'ParentChild',
    State => 'Valid', UserID => 1 ) or die 'LinkAdd ticket-ticket failed';
$LinkObject->LinkAdd( SourceObject => 'Ticket', SourceKey => {z["A"]["id"]},
    TargetObject => 'ITSMConfigItem', TargetKey => {ci["id"]}, Type => 'RelevantTo',
    State => 'Valid', UserID => 1 ) or die 'LinkAdd ticket-CI failed';
Emit({{ok => 1}});
"""
        )

        async def tiqora_add(session: AsyncSession, provider: ZnunyCiProvider) -> None:
            assert await link_objects(
                session,
                source_object="Ticket",
                source_key=str(t["A"]["id"]),
                target_object="Ticket",
                target_key=str(t["B"]["id"]),
                link_type="ParentChild",
                user_id=USER_ID,
            )
            assert await provider.link_ticket(
                session, ticket_id=t["A"]["id"], ref=ref, link_type="RelevantTo", user_id=USER_ID
            )

        await _commit(golden_session_factory, tiqora_add)
        znuny_state = _link_state(golden_conn, z)
        tiqora_state = _link_state(golden_conn, t)
        assert tiqora_state == znuny_state
        # not vacuous: both link rows and the history of both ends exist
        assert len(znuny_state["links"]) == 2
        assert {h[0] for h in znuny_state["history_A"]} == {"TicketLinkAdd", "CILinkAdd"}
        assert [h[0] for h in znuny_state["history_B"]] == ["TicketLinkAdd"]

        # --- delete --------------------------------------------------------------------------
        znuny_json(
            f"""
$LinkObject->LinkDelete( Object1 => 'Ticket', Key1 => {z["A"]["id"]},
    Object2 => 'Ticket', Key2 => {z["B"]["id"]}, Type => 'ParentChild', UserID => 1 )
    or die 'LinkDelete ticket-ticket failed';
$LinkObject->LinkDelete( Object1 => 'Ticket', Key1 => {z["A"]["id"]},
    Object2 => 'ITSMConfigItem', Key2 => {ci["id"]}, Type => 'RelevantTo', UserID => 1 )
    or die 'LinkDelete ticket-CI failed';
Emit({{ok => 1}});
"""
        )

        async def tiqora_delete(session: AsyncSession, provider: ZnunyCiProvider) -> None:
            assert (
                await unlink_objects(
                    session,
                    object1="Ticket",
                    key1=str(t["A"]["id"]),
                    object2="Ticket",
                    key2=str(t["B"]["id"]),
                    link_type="ParentChild",
                    user_id=USER_ID,
                )
                == 1
            )
            assert (
                await provider.unlink_ticket(
                    session,
                    ticket_id=t["A"]["id"],
                    ref=ref,
                    link_type="RelevantTo",
                    user_id=USER_ID,
                )
                == 1
            )

        await _commit(golden_session_factory, tiqora_delete)
        znuny_state = _link_state(golden_conn, z)
        tiqora_state = _link_state(golden_conn, t)
        assert tiqora_state == znuny_state
        assert znuny_state["links"] == []
        assert {h[0] for h in znuny_state["history_A"]} == {
            "TicketLinkAdd",
            "TicketLinkDelete",
            "CILinkAdd",
            "CILinkDelete",
        }
    finally:
        znuny_sync()
        znuny_json(
            "for my $ID ("
            + ",".join(str(x["id"]) for x in (*z.values(), *t.values()))
            + """) { $LinkObject->LinkDeleteAll( Object => 'Ticket', Key => $ID, UserID => 1 ); }
Emit({ok => 1});"""
        )


# =================================================================================================
# the documented difference: configitem_history
# =================================================================================================


def _ci_history(conn: pymysql.Connection, ci_id: int) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT h.id, t.name, h.content FROM configitem_history h"
            " JOIN configitem_history_type t ON t.id = h.type_id"
            " WHERE h.configitem_id = %s ORDER BY h.id",
            (ci_id,),
        )
        return list(cur.fetchall())


@pytest.mark.itsm_peer
async def test_configitem_history_difference_documented(
    peer: Peer,
    itsm_seed: dict[str, Any],
    golden_session_factory: SessionFactory,
    golden_conn: pymysql.Connection,
) -> None:
    """Open question 1: Znuny's CI history gets ``LinkAdd`` / ``LinkDelete``, Tiqora's links do not.

    Upstream: ``Kernel/System/LinkObject/ITSMConfigItem.pm`` (``LinkAddPost`` / ``LinkDeletePost``
    fire the CI ``LinkAdd`` / ``LinkDelete`` event, handled by
    ``ITSMConfigItem/Event/DoHistory.pm``)
    in ITSMConfigurationManagement 6.5.4 and 7.3.5 (identical files). Mode A never writes an ITSM
    table, so the entry is missing for links made in Tiqora; the ticket history is the only trace.
    The assertions pin both sides so a change on either is noticed.
    """
    ci = itsm_seed["cis"]["db01"]
    ref = CiRef("znuny", ci["id"])
    zt = znuny_json("Emit(NewTicket('itsm28 ci-history z'));")
    tt = znuny_json("Emit(NewTicket('itsm28 ci-history t'));")

    def new_entries(before: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
        return [(name, content) for _id, name, content in _ci_history(golden_conn, ci["id"])][
            len(before) :
        ]

    try:
        before = _ci_history(golden_conn, ci["id"])
        znuny_json(
            f"""
$LinkObject->LinkAdd( SourceObject => 'Ticket', SourceKey => {zt["id"]},
    TargetObject => 'ITSMConfigItem', TargetKey => {ci["id"]}, Type => 'RelevantTo',
    State => 'Valid', UserID => 1 ) or die 'LinkAdd failed';
Emit({{ok => 1}});
"""
        )
        assert new_entries(before) == [("LinkAdd", f"{zt['id']}%%Ticket")]

        mark = _ci_history(golden_conn, ci["id"])
        await _commit(
            golden_session_factory,
            lambda s, p: p.link_ticket(
                s, ticket_id=tt["id"], ref=ref, link_type="RelevantTo", user_id=USER_ID
            ),
        )
        assert new_entries(mark) == []  # Tiqora: no CI history entry (the difference)

        mark = _ci_history(golden_conn, ci["id"])
        znuny_json(
            f"""
$LinkObject->LinkDelete( Object1 => 'Ticket', Key1 => {zt["id"]},
    Object2 => 'ITSMConfigItem', Key2 => {ci["id"]}, Type => 'RelevantTo', UserID => 1 )
    or die 'LinkDelete failed';
Emit({{ok => 1}});
"""
        )
        assert new_entries(mark) == [("LinkDelete", f"{zt['id']}%%Ticket")]

        mark = _ci_history(golden_conn, ci["id"])
        await _commit(
            golden_session_factory,
            lambda s, p: p.unlink_ticket(
                s, ticket_id=tt["id"], ref=ref, link_type=None, user_id=USER_ID
            ),
        )
        assert new_entries(mark) == []
    finally:
        znuny_sync()
        znuny_json(
            f"for my $ID ({zt['id']}, {tt['id']}) "
            "{ $LinkObject->LinkDeleteAll( Object => 'Ticket', Key => $ID, UserID => 1 ); }"
            " Emit({ok => 1});"
        )


# =================================================================================================
# priority matrix
# =================================================================================================


@pytest.mark.itsm_peer
async def test_priority_matrix_equals_znuny(
    peer: Peer, golden_session_factory: SessionFactory
) -> None:
    """``PriorityAllocationGet`` for all 25 cells equals Tiqora's Mode A matrix and lookup."""
    from tiqora.itsm.mode import get_itsm_mode
    from tiqora.itsm.priority import load_matrix, lookup, suggest_priority

    znuny = znuny_json(
        r"""
my $DFObject = $Kernel::OM->Get('Kernel::System::DynamicField');
my $CIP      = $Kernel::OM->Get('Kernel::System::ITSMCIPAllocate');
my $Crit     = $DFObject->DynamicFieldGet( Name => 'ITSMCriticality' );
my $Impact   = $DFObject->DynamicFieldGet( Name => 'ITSMImpact' );
my @CritKeys   = sort keys %{ $Crit->{Config}->{PossibleValues} };
my @ImpactKeys = sort keys %{ $Impact->{Config}->{PossibleValues} };
my %Cells;
for my $C (@CritKeys) {
    for my $I (@ImpactKeys) {
        $Cells{"$C|$I"} = $CIP->PriorityAllocationGet( Criticality => $C, Impact => $I );
    }
}
Emit({ criticality => \@CritKeys, impact => \@ImpactKeys, cells => \%Cells });
"""
    )
    assert len(znuny["cells"]) == 25
    assert all(v for v in znuny["cells"].values())  # every cell has a priority
    # the three distinct priorities the stock matrix maps to: it is not a constant table
    assert len({v for v in znuny["cells"].values()}) > 1

    async with golden_session_factory() as session:
        mode = await get_itsm_mode(session, fresh=True)
        assert mode.mode == "znuny_readthrough"
        settings = ItsmSettings()
        view = await load_matrix(session, mode, settings)
        assert view.source == "znuny" and view.editable is False
        assert sorted(view.criticality_values) == znuny["criticality"]
        assert sorted(view.impact_values) == znuny["impact"]
        tiqora = {f"{c.criticality}|{c.impact}": c.priority_id for c in view.cells}
        assert {k: str(v) for k, v in tiqora.items()} == {
            k: str(v) for k, v in znuny["cells"].items()
        }
        for key, priority_id in znuny["cells"].items():
            criticality, impact = key.split("|")
            assert lookup(view.cells, criticality, impact) == int(priority_id)
            assert await suggest_priority(
                session, mode, settings, criticality=criticality, impact=impact
            ) == int(priority_id)
        assert lookup(view.cells, "", "3 normal") is None  # Znuny: "Need Criticality!" -> undef
        assert lookup(view.cells, "9 unknown", "3 normal") is None


# =================================================================================================
# service incident state
# =================================================================================================

_SERVICE_FIXTURE_PERL = r"""
my $GC      = $Kernel::OM->Get('Kernel::System::GeneralCatalog');
my $Service = $Kernel::OM->Get('Kernel::System::Service');
sub Catalog { my ($Class) = @_; return { reverse %{ $GC->ItemList( Class => $Class ) } }; }
my $Class = Catalog('ITSM::ConfigItem::Class');
my $Depl  = Catalog('ITSM::ConfigItem::DeploymentState');
my $Inci  = Catalog('ITSM::Core::IncidentState');
my $Types = Catalog('ITSM::Service::Type');

sub FindCI {
    my ($Name) = @_;
    my $IDs = $CIObject->ConfigItemSearchExtended( Name => $Name );
    return $IDs && @{$IDs} ? $IDs->[0] + 0 : undef;
}

sub EnsureCI {
    my ($Name) = @_;
    my $ID = FindCI($Name);
    return $ID if $ID;
    my $ClassID = $Class->{Computer};
    $ID = $CIObject->ConfigItemAdd( ClassID => $ClassID, UserID => 1 )
        or die 'ConfigItemAdd failed';
    my $Definition = $CIObject->DefinitionGet( ClassID => $ClassID );
    $CIObject->VersionAdd(
        ConfigItemID => $ID, Name => $Name, DefinitionID => $Definition->{DefinitionID},
        DeplStateID => $Depl->{Production}, InciStateID => $Inci->{Operational},
        XMLData => [ undef, { Version => [ undef, { FQDN => [ undef, { Content => $Name } ] } ] } ],
        UserID => 1,
    ) or die "VersionAdd $Name failed";
    return $ID + 0;
}

sub EnsureService {
    my ( $Name, $ParentID ) = @_;
    my $Full = $ParentID ? $Service->ServiceLookup( ServiceID => $ParentID ) . "::$Name" : $Name;
    my $ID = $Service->ServiceLookup( Name => $Full );
    return $ID + 0 if $ID;
    return $Service->ServiceAdd(
        Name => $Name, ParentID => $ParentID, ValidID => 1, TypeID => $Types->{'End User Service'},
        Criticality => '3 normal', UserID => 1,
    ) + 0;
}

sub SetCI {
    my ( $ID, $DeplName, $InciName ) = @_;
    my $V = $CIObject->VersionGet( ConfigItemID => $ID, XMLDataGet => 1 );
    return 1 if $V->{DeplState} eq $DeplName && $V->{InciState} eq $InciName;
    return $CIObject->VersionAdd(
        ConfigItemID => $ID, Name => $V->{Name}, DefinitionID => $V->{DefinitionID},
        DeplStateID => $Depl->{$DeplName}, InciStateID => $Inci->{$InciName},
        XMLData => $V->{XMLData}, UserID => 1,
    ) or die "VersionAdd (state) failed for $ID";
}

sub EnsureLink {
    my (%P) = @_;
    return 1 if $LinkObject->LinkList(
        Object => $P{SO}, Key => $P{SK}, Object2 => $P{TO}, State => 'Valid',
        Type => $P{Type}, UserID => 1,
    )->{ $P{TO} };
    return $LinkObject->LinkAdd(
        SourceObject => $P{SO}, SourceKey => $P{SK}, TargetObject => $P{TO}, TargetKey => $P{TK},
        Type => $P{Type}, State => 'Valid', UserID => 1,
    ) or die 'LinkAdd failed';
}

my $Parent = EnsureService( 'itsm28-svc' );
my $Child  = EnsureService( 'itsm28-kid', $Parent );
my $CiA    = EnsureCI('itsm28-a.example.org');
my $CiB    = EnsureCI('itsm28-b.example.org');
my $CiC    = EnsureCI('itsm28-c.example.org');
EnsureLink( SO => 'ITSMConfigItem', SK => $CiA, TO => 'Service', TK => $Parent,
    Type => 'DependsOn' );
EnsureLink( SO => 'ITSMConfigItem', SK => $CiB, TO => 'Service', TK => $Child,
    Type => 'DependsOn' );
EnsureLink( SO => 'ITSMConfigItem', SK => $CiA, TO => 'ITSMConfigItem', TK => $CiC,
    Type => 'DependsOn' );
"""


def _set_states(ids: dict[str, int], states: dict[str, tuple[str, str]]) -> dict[str, Any]:
    """Set (deployment, incident) state names on the three fixture CIs, return Znuny's view."""
    body = _SERVICE_FIXTURE_PERL + "\n".join(
        f"SetCI( {ids[key]}, '{depl}', '{inci}' );" for key, (depl, inci) in states.items()
    )
    body += r"""
sub Svc {
    my ($ID) = @_;
    my %S = $Service->ServiceGet( ServiceID => $ID, IncidentState => 1, UserID => 1 );
    return { type => $S{CurInciStateType}, state => $S{CurInciState} };
}
sub CiState {
    my ($ID) = @_;
    my $CI = $CIObject->ConfigItemGet( ConfigItemID => $ID, Cache => 0 );
    return $CI->{CurInciStateType};
}
Emit({
    services => { parent => Svc($Parent), child => Svc($Child) },
    cis      => { a => CiState($CiA), b => CiState($CiB), c => CiState($CiC) },
});
"""
    result: dict[str, Any] = znuny_json(body)
    return result


@pytest.mark.itsm_peer
async def test_service_incident_state_equals_znuny(
    peer: Peer, golden_session_factory: SessionFactory
) -> None:
    """Set CI states in Znuny: ``ServiceGet(IncidentState => 1)`` equals Tiqora's live state.

    Own fixture (service ``itsm28-svc`` with child ``itsm28-kid``, CIs ``itsm28-a|b|c``, links
    CI-a -> service, CI-b -> child, CI-a DependsOn CI-c), changed through ``VersionAdd`` so Znuny
    recalculates its stored CI states. The scenarios cover productive and not productive CIs, all
    three states, the transitive CI-to-CI warning and the child-service rule.
    """
    ids_raw = znuny_json(
        _SERVICE_FIXTURE_PERL
        + "Emit({ parent => $Parent, child => $Child, a => $CiA, b => $CiB, c => $CiC });"
    )
    ids = {k: int(v) for k, v in ids_raw.items()}
    prod = "Production"
    scenarios: list[dict[str, tuple[str, str]]] = [
        {"a": (prod, "Operational"), "b": (prod, "Operational"), "c": (prod, "Operational")},
        {"a": (prod, "Warning"), "b": (prod, "Operational"), "c": (prod, "Operational")},
        {"a": (prod, "Incident"), "b": (prod, "Operational"), "c": (prod, "Operational")},
        # a CI that is not productive does not count
        {"a": ("Planned", "Incident"), "b": (prod, "Operational"), "c": (prod, "Operational")},
        # the child service in incident: the parent turns to warning
        {"a": (prod, "Operational"), "b": (prod, "Incident"), "c": (prod, "Operational")},
        {"a": (prod, "Operational"), "b": (prod, "Warning"), "c": (prod, "Operational")},
        # transitive: CI-a depends on CI-c, which is in incident
        {"a": (prod, "Operational"), "b": (prod, "Operational"), "c": (prod, "Incident")},
        {"a": (prod, "Operational"), "b": (prod, "Operational"), "c": (prod, "Operational")},
    ]
    seen_parent: set[str] = set()
    seen_child: set[str] = set()
    for scenario in scenarios:
        znuny = _set_states(ids, scenario)
        async with golden_session_factory() as session:
            states = await make_provider(session).service_incident_states(
                session, [ids["parent"], ids["child"]]
            )
        for role in ("parent", "child"):
            got = states[ids[role]].state
            assert got is not None, (scenario, role)
            assert got.functionality == znuny["services"][role]["type"], (scenario, role)
            assert got.name == znuny["services"][role]["state"], (scenario, role)
        seen_parent.add(znuny["services"]["parent"]["type"])
        seen_child.add(znuny["services"]["child"]["type"])
    # not vacuous: every state of the parent and the child was produced and compared
    assert seen_parent == {"operational", "warning", "incident"}
    assert {"operational", "warning", "incident"} >= seen_child >= {"operational", "incident"}


# =================================================================================================
# setup: the rows Znuny's ITSM packages create
# =================================================================================================


class _ZnunyYamlLoader(yaml.SafeLoader):
    """SafeLoader that keeps ``No``/``Yes`` strings: Znuny writes them as plain scalars."""


_ZnunyYamlLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_ZnunyYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf")
)


def _stringified(value: Any) -> Any:
    """Leaf values as text: Perl's JSON/YAML hand back numbers and numeric strings alike."""
    if isinstance(value, dict):
        return {str(k): _stringified(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringified(v) for v in value]
    return "" if value is None else str(value)


def _assert_setup_rows(conn: pymysql.Connection, seed: SeedSet, *, packages: bool) -> None:
    """The ticket types, the workaround state and the dynamic fields equal the seed definitions.

    ``packages=True`` additionally checks what only the ITSM packages create and Tiqora's setup
    does not (ticket type ``RfC``, history types ``CILinkAdd`` / ``CILinkDelete``, the ``itsm-*``
    groups).
    """
    with conn.cursor() as cur:
        # RfC belongs to ITSMChangeManagement: Tiqora's setup creates its own change types instead
        wanted_types = [t.name for t in seed.ticket_types if packages or t.name != "RfC"]
        for ticket_type in wanted_types:
            cur.execute("SELECT valid_id FROM ticket_type WHERE name = %s", (ticket_type,))
            row = cur.fetchone()
            assert row is not None and row[0] == 1, f"ticket type {ticket_type}"
        for state in seed.ticket_states:
            cur.execute(
                "SELECT t.name, s.comments, s.valid_id FROM ticket_state s"
                " JOIN ticket_state_type t ON t.id = s.type_id WHERE s.name = %s",
                (state.name,),
            )
            assert cur.fetchone() == (state.type_name, state.comments, 1), state.name
        for field in seed.dynamic_fields:
            cur.execute(
                "SELECT label, field_type, object_type, field_order, internal_field, valid_id,"
                " config FROM dynamic_field WHERE name = %s",
                (field.name,),
            )
            row = cur.fetchone()
            assert row is not None, f"dynamic field {field.name}"
            label, field_type, object_type, order, internal, valid, config = row
            assert (label, field_type, object_type) == (
                field.label,
                field.field_type,
                field.object_type,
            )
            assert (order, internal, valid) == (field.field_order, 1, 1), field.name
            raw = config.decode() if isinstance(config, bytes) else config
            assert yaml.load(raw, Loader=_ZnunyYamlLoader) == field.config, field.name  # noqa: S506
        if packages:
            names = tuple(h.name for h in seed.ticket_history_types)
            cur.execute("SELECT name FROM ticket_history_type WHERE name IN %s", (names,))
            assert {r[0] for r in cur.fetchall()} == set(names)
            wanted = {g.name: g.comments for g in seed.groups}
            cur.execute(
                "SELECT name, comments FROM permission_groups WHERE name IN %s",
                (tuple(wanted),),
            )
            assert dict(cur.fetchall()) == wanted


def _checksums(conn: pymysql.Connection, tables: tuple[str, ...]) -> dict[str, int | None]:
    with conn.cursor() as cur:
        cur.execute("CHECKSUM TABLE " + ", ".join(tables))  # tables are constants of this module
        return {str(name).split(".")[-1]: checksum for name, checksum in cur.fetchall()}


@pytest.mark.itsm_peer
async def test_setup_on_itsm_peer_is_noop(
    peer: Peer, golden_session_factory: SessionFactory, golden_conn: pymysql.Connection
) -> None:
    """On a site with Znuny ITSM the setup finds everything, creates nothing, and the seed
    definitions equal the rows the packages created (ids aside).

    The ``change`` group (types ``Change`` / ``Change::Task``) is Tiqora's own addition: the
    packages do not create those types, so it stays ``missing`` and is never applied here. The
    plain-peer counterpart is ``test_setup_on_plain_peer_equals_itsm_packages``.
    """
    from tiqora.itsm.setup import apply_setup, plan_setup, setup_complete

    seed = for_profile(peer.profile)
    _assert_setup_rows(golden_conn, seed, packages=True)

    tables = ("ticket_type", "ticket_state", "dynamic_field", "dynamic_field_value")
    before = _checksums(golden_conn, tables)
    async with golden_session_factory() as session:
        settings = ItsmSettings()
        plan = await plan_setup(session, settings)
        by_group = {g: [i for i in plan if i.group == g] for g in ("incident_problem", "change")}
        assert by_group["incident_problem"], "the plan lists the incident / problem rows"
        assert {i.status for i in by_group["incident_problem"]} == {"present"}
        assert {(i.kind, i.status) for i in by_group["change"]} == {("ticket_type", "missing")}
        assert await setup_complete(session, settings) is True
        after_plan = await apply_setup(
            session, settings, groups={"incident_problem"}, user_id=USER_ID
        )
        await session.commit()
    assert [i for i in after_plan if i.group == "incident_problem" and i.status != "present"] == []
    assert _checksums(golden_conn, tables) == before


_SEED_TYPE_NAMES = ("Incident", "Incident::Major", "ServiceRequest", "Problem")


def _remove_setup_rows(conn: pymysql.Connection, seed: SeedSet) -> None:
    """Delete what ``apply_setup`` created on a PLAIN peer (never runs against an ITSM peer)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables"
            " WHERE table_schema = DATABASE() AND table_name = 'configitem'"
        )
        fetched = cur.fetchone()
        assert fetched is not None and fetched[0] == 0, "refusing to clean up on an ITSM peer"
        names = tuple(f.name for f in seed.dynamic_fields)
        cur.execute(
            "DELETE FROM dynamic_field_value WHERE field_id IN"
            " (SELECT id FROM dynamic_field WHERE name IN %s)",
            (names,),
        )
        cur.execute("DELETE FROM dynamic_field WHERE name IN %s", (names,))
        cur.execute("DELETE FROM ticket_type WHERE name IN %s", (_SEED_TYPE_NAMES,))
        cur.execute(
            "DELETE FROM ticket_state WHERE name IN %s",
            (tuple(s.name for s in seed.ticket_states),),
        )


@pytest.fixture()
async def plain_peer_setup(
    peer_plain: Peer, golden_session_factory: SessionFactory, golden_conn: pymysql.Connection
) -> AsyncIterator[SeedSet]:
    """Tiqora's ITSM setup applied on a plain peer; the rows are removed again afterwards."""
    from tiqora.itsm.setup import apply_setup

    seed = for_profile(peer_plain.profile)
    _remove_setup_rows(golden_conn, seed)
    try:
        async with golden_session_factory() as session:
            plan = await apply_setup(
                session, ItsmSettings(), groups={"incident_problem"}, user_id=USER_ID
            )
            await session.commit()
        assert {i.status for i in plan if i.group == "incident_problem"} == {"present"}
        znuny_sync()  # the daemon's TiqoraSync run: cache types of the new master data
        yield seed
    finally:
        _remove_setup_rows(golden_conn, seed)
        znuny_sync()
        znuny_console("Maint::Cache::Delete")


@pytest.fixture(scope="module")
def peer_plain(golden_peer_id: str) -> Peer:
    """The plain peer of this session (tests using it carry the ``plain_peer`` marker)."""
    base = resolve_peer(golden_peer_id)["GOLDEN_BASE_PEER"]
    profile, core, cmdb = EXPECTED_PEERS[base]
    return Peer(golden_peer_id, base, profile, core, cmdb)


@pytest.mark.plain_peer
async def test_setup_on_plain_peer_equals_itsm_packages(
    plain_peer_setup: SeedSet, golden_conn: pymysql.Connection
) -> None:
    """Without Znuny ITSM the setup writes exactly the definitions the packages create.

    ``test_setup_on_itsm_peer_is_noop`` proves the seed definitions equal the package rows on
    the ITSM peers; here the rows Tiqora wrote are compared with the same definitions (type
    names, state type and comment, label / type / order / internal flag / config of every
    dynamic field).
    """
    _assert_setup_rows(golden_conn, plain_peer_setup, packages=False)


@pytest.mark.plain_peer
async def test_znuny_reads_tiqora_seeded_dynamic_fields(plain_peer_setup: SeedSet) -> None:
    """Znuny loads what Tiqora's setup wrote: ``DynamicFieldGet``, ``YAML->Load``, validation."""
    names = [f.name for f in plain_peer_setup.dynamic_fields]
    znuny = znuny_json(
        "my @Names = ("
        + ",".join(f"'{n}'" for n in names)
        + r""");
my $DFObject = $Kernel::OM->Get('Kernel::System::DynamicField');
my $Backend  = $Kernel::OM->Get('Kernel::System::DynamicField::Backend');
my $YAML     = $Kernel::OM->Get('Kernel::System::YAML');
my %Out;
for my $Name (@Names) {
    my $DF = $DFObject->DynamicFieldGet( Name => $Name ) or die "DynamicFieldGet $Name failed";
    my $Rows = Rows( SQL => 'SELECT config FROM dynamic_field WHERE name = ?', Bind => [ \$Name ] );
    my $Loaded = $YAML->Load( Data => $Rows->[0]->[0] );
    $Out{$Name} = {
        label => $DF->{Label}, field_type => $DF->{FieldType}, object_type => $DF->{ObjectType},
        order => $DF->{FieldOrder}, internal => $DF->{InternalField}, valid => $DF->{ValidID},
        config => $DF->{Config}, yaml => $Loaded,
    };
}
my $List = $DFObject->DynamicFieldListGet( ObjectType => 'Ticket', Valid => 1 ) || [];
my $Crit = $DFObject->DynamicFieldGet( Name => 'ITSMCriticality' );
my $Valid = $Backend->ValueValidate( DynamicFieldConfig => $Crit, Value => '4 high', UserID => 1 );
my $Possible = $Backend->PossibleValuesGet( DynamicFieldConfig => $Crit ) || {};
my $TypeObject  = $Kernel::OM->Get('Kernel::System::Type');
my $StateObject = $Kernel::OM->Get('Kernel::System::State');
my %State = $StateObject->StateGet( Name => 'closed with workaround' );
Emit({
    fields => \%Out,
    listed => [ map { $_->{Name} } @{$List} ],
    valid  => $Valid ? 1 : 0, possible => [ sort keys %{$Possible} ],
    types  => { map { $_ => ( $TypeObject->TypeLookup( Type => $_ ) // 0 ) + 0 }
        ( 'Incident', 'Incident::Major', 'ServiceRequest', 'Problem' ) },
    state  => { name => $State{Name}, type => $State{TypeName}, valid => $State{ValidID} },
});"""
    )
    for field in plain_peer_setup.dynamic_fields:
        got = znuny["fields"][field.name]
        assert (got["label"], got["field_type"], got["object_type"]) == (
            field.label,
            field.field_type,
            field.object_type,
        )
        assert (int(got["order"]), int(got["internal"]), int(got["valid"])) == (
            field.field_order,
            1,
            1,
        )
        # the parsed config Znuny hands out and the YAML in the column are the seed config
        assert _stringified(got["config"]) == _stringified(field.config), field.name
        assert _stringified(got["yaml"]) == _stringified(field.config), field.name
    assert set(names) <= set(znuny["listed"])
    criticality = next(f for f in plain_peer_setup.dynamic_fields if f.name == "ITSMCriticality")
    assert znuny["valid"] == 1
    # PossibleNone adds the empty choice
    assert [k for k in znuny["possible"] if k] == sorted(criticality.config["PossibleValues"])
    assert ("" in znuny["possible"]) == bool(criticality.config["PossibleNone"])
    assert all(znuny["types"][n] > 0 for n in _SEED_TYPE_NAMES)
    seed_state = plain_peer_setup.ticket_states[0]
    assert znuny["state"] == {"name": seed_state.name, "type": seed_state.type_name, "valid": 1}


# =================================================================================================
# Mode A writes no ITSM table
# =================================================================================================


@pytest.mark.itsm_peer
async def test_mode_a_writes_no_itsm_table_on_peer(
    peer: Peer,
    itsm_seed: dict[str, Any],
    golden_session_factory: SessionFactory,
    golden_conn: pymysql.Connection,
) -> None:
    """A full Mode A pass (reads, link, unlink, setup, matrix) leaves every ITSM table as it was.

    The tables are the schema profile's ``readonly_tables`` (CIs, versions, definitions, history,
    catalog, attributes, matrix, ``service`` / ``sla`` with their ITSM columns) compared with
    ``CHECKSUM TABLE`` before and after. The pass must really write (``link_relation`` changes).
    """
    from tiqora.itsm.mode import get_itsm_mode
    from tiqora.itsm.priority import load_matrix
    from tiqora.itsm.setup import apply_setup
    from tiqora.itsm.znuny_adapter.profiles import PROFILES

    readonly = PROFILES[peer.profile].readonly_tables
    ticket = znuny_json("Emit(NewTicket('itsm28 no itsm write'));")
    ci = CiRef("znuny", itsm_seed["cis"]["web01"]["id"])
    service_id = int(itsm_seed["service_id"])
    before = _checksums(golden_conn, readonly)
    assert all(v is not None for v in before.values()), before  # every table exists
    link_before = _checksums(golden_conn, ("link_relation", "ticket_history"))

    try:
        async with golden_session_factory() as session:
            provider = make_provider(session)
            page = await provider.search(session, CiQuery(text="web01"), user_id=USER_ID)
            assert page.items
            await provider.get(session, ci, user_id=USER_ID)
            await provider.versions(session, ci, user_id=USER_ID, limit=10, offset=0)
            await provider.service_incident_states(session, [service_id])
            await provider.service_cis(session, service_id, user_id=USER_ID)
            assert await provider.link_ticket(
                session, ticket_id=ticket["id"], ref=ci, link_type="RelevantTo", user_id=USER_ID
            )
            mode = await get_itsm_mode(session, fresh=True)
            await load_matrix(session, mode, ItsmSettings())
            await apply_setup(session, ItsmSettings(), groups={"incident_problem"}, user_id=USER_ID)
            await session.commit()
        assert _checksums(golden_conn, ("link_relation", "ticket_history")) != link_before
    finally:
        async with golden_session_factory() as session:
            await make_provider(session).unlink_ticket(
                session, ticket_id=ticket["id"], ref=ci, link_type=None, user_id=USER_ID
            )
            await session.commit()
    assert _checksums(golden_conn, readonly) == before
