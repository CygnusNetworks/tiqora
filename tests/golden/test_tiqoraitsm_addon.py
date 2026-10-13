"""Golden-master: TiqoraITSM 1.0.0 (ticket zoom widget "Betroffene CIs (Tiqora)") in a real Znuny.

The peer image does not install the add-on, so every file of ``packages/znuny-addon/TiqoraITSM``
is copied in with ``docker compose cp`` (what the OPM install does), the SysConfig is rebuilt with
``Maint::Config::Rebuild`` and the widget is driven the way the ticket zoom does it:
``Kernel::Output::HTML::TicketZoom::TiqoraITSM->new(UserID => ...)->Run(Ticket => {...})`` with a
``Kernel::Output::HTML::Layout`` of that user. Proves, on the shared golden DB:

- Mode B (``itsm.mode`` = ``tiqora``): the widget lists the CIs linked to the ticket, with a link
  ``<BaseURL>/agent/cmdb/<ci_id>`` (the route Tiqora's CMDB registers), states and escaped names;
- an agent without read access to the group of a CI's class does not see that CI;
- every other mode, and a missing ``itsm.mode`` row, leave the widget empty (the add-on never
  guesses from the Znuny ``configitem`` table);
- missing views are quiet (empty, no error);
- ``ReadOnly()`` follows ``itsm.znuny_readonly``; ``CustomerCIs()`` returns portal-visible classes
  only, header fields only;
- the read-only guard (``PreApplicationModule###TiqoraITSMReadOnly``) refuses the CI write screens
  and ``AgentLinkObject`` towards CIs once the flag is set, and lets read screens through;
- the customer page lists the customer's own CIs, company CIs (also through
  ``customer_user_customer``) and nothing from a class that is not portal-visible;
- the optional linking (``AgentTiqoraITSMLink``) posts cmdb's ``LinkIn`` body with the bearer key
  to the recording sink, refuses without POST / challenge token / ticket ``rw`` / the setting.

The golden DB may lack the CMDB tables or views: a module fixture creates what is absent and drops
it again; otherwise it removes only its own rows. Run on both peers:
``GOLDEN=1 GOLDEN_PEER=znuny-6.5 just golden-test tests/golden/test_tiqoraitsm_addon.py`` and
``znuny-7.3`` (an ITSM peer works as well: the add-on does not look at ``configitem``).
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from _helpers import (
    gisink_clear,
    gisink_requests,
    gisink_respond,
    znuny_console,
    znuny_copy_in,
    znuny_perl_eval,
)
from sqlalchemy import bindparam, create_engine, delete, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.golden

ADDON = Path(__file__).resolve().parents[2] / "packages/znuny-addon/TiqoraITSM"
PEER_HOME = "/opt/otrs"
# every file of the package (``Filelist`` of the .sopm), relative to the package root
FILES = (
    "Kernel/System/TiqoraITSM.pm",
    "Kernel/Output/HTML/TicketZoom/TiqoraITSM.pm",
    "Kernel/Output/HTML/Templates/Standard/AgentTicketZoom/TiqoraITSM.tt",
    "Kernel/Modules/TiqoraITSMReadOnly.pm",
    "Kernel/Modules/CustomerTiqoraITSM.pm",
    "Kernel/Modules/AgentTiqoraITSMLink.pm",
    "Kernel/Output/HTML/Templates/Standard/CustomerTiqoraITSM.tt",
    "Kernel/Language/de_TiqoraITSM.pm",
    "Kernel/Config/Files/XML/TiqoraITSM.xml",
)
SETTING_KEYS = ("itsm.mode", "itsm.znuny_readonly")

AGENT_LOGIN = "itim56.agent"
GROUP_SECRET = "itim56-geheim"
CUSTOMER_LOGIN = "itim56.kunde@example.org"
TICKET_ID = 9_900_056  # no ticket row needed: the add-on only reads the link views
BASE_URL = "https://tiqora.example.org"
PREFIX = "itim56"  # class keys, CI numbers and catalog keys of this module
COMPANY = "itim56-firma"  # primary CustomerID passed to CustomerCIs / the customer page
COMPANY_MAPPED = "itim56-mapped"  # reached only through customer_user_customer
SINK_URL = "http://gisink:8080"  # the golden recording sink, as the peer reaches it
API_KEY = "itim56-test-key"
CHALLENGE_TOKEN = "itim56-challenge-token"
TICKET_TITLE = "itim56 TiqoraITSM linking"

_PERL_HEAD = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden'});
"""

_USERS = (
    _PERL_HEAD
    + rf"""
$Kernel::OM->Get('Kernel::Config')->Set(Key => 'CheckMXRecord', Value => 0);  # no DNS in the peer
$Kernel::OM->Get('Kernel::Config')->Set(Key => 'CheckEmailInvalidAddress', Value => '^\\$');
my $Group = $Kernel::OM->Get('Kernel::System::Group');
my $User  = $Kernel::OM->Get('Kernel::System::User');
my %Groups = reverse $Group->GroupList(Valid => 0);
my $Secret = $Groups{{'{GROUP_SECRET}'}} // $Group->GroupAdd(
    Name => '{GROUP_SECRET}', ValidID => 1, UserID => 1) or die 'GroupAdd failed';
my $UID = $User->UserLookup(UserLogin => '{AGENT_LOGIN}', Silent => 1)
    // $User->UserAdd(
        UserFirstname => 'Itim56', UserLastname => 'Agent', UserLogin => '{AGENT_LOGIN}',
        UserEmail => 'itim56.agent@example.org', ValidID => 1, ChangeUserID => 1,
    ) or die 'UserAdd failed';
$Group->PermissionGroupUserAdd(
    GID => $Groups{{users}}, UID => $UID, UserID => 1,
    Permission => {{ ro => 1, move_into => 0, create => 0, owner => 0, priority => 0, rw => 0 }},
) or die 'PermissionGroupUserAdd failed';
print JSON::PP::encode_json({{
    secret => $Secret + 0, users => $Groups{{users}} + 0, uid => $UID + 0 }});
"""
).replace(
    "use Kernel::System::ObjectManager;", "use Kernel::System::ObjectManager; use JSON::PP;", 1
)

_WIDGET = (
    _PERL_HEAD
    + r"""
use Kernel::Output::HTML::TicketZoom::TiqoraITSM;
my $UID = $Kernel::OM->Get('Kernel::System::User')->UserLookup(UserLogin => '__LOGIN__');
$Kernel::OM->ObjectParamAdd('Kernel::Output::HTML::Layout' => {UserID => $UID, Lang => 'de'});
$Kernel::OM->Get('Kernel::Output::HTML::Layout');
my $Widget = Kernel::Output::HTML::TicketZoom::TiqoraITSM->new(UserID => $UID);
my $Result = $Widget->Run(Ticket => {TicketID => __TICKET__}, Config => {});
binmode STDOUT, ':encoding(UTF-8)';
print( ( ref $Result ? $Result->{Output} : '' ) // '' );
"""
)

_EVAL = (
    _PERL_HEAD
    + r"""
binmode STDOUT, ':encoding(UTF-8)';
my $Add = $Kernel::OM->Get('Kernel::System::TiqoraITSM');
__CODE__
"""
)


def _widget_output(ticket_id: int, user_login: str) -> str:
    code = _WIDGET.replace("__LOGIN__", user_login).replace("__TICKET__", str(ticket_id))
    return znuny_perl_eval(code)


def _addon_eval(code: str) -> str:
    return znuny_perl_eval(_EVAL.replace("__CODE__", code))


_LAYOUT = r"""
$Kernel::OM->ObjectParamAdd('Kernel::Output::HTML::Layout' => {
    UserID => $UID, UserLogin => '__LOGIN__', Lang => 'de', UserChallengeToken => '__TOKEN__' });
"""

_PRERUN = (
    _PERL_HEAD
    + r"""
use Kernel::Modules::TiqoraITSMReadOnly;
binmode STDOUT, ':encoding(UTF-8)';
my $UID = $Kernel::OM->Get('Kernel::System::User')->UserLookup(UserLogin => '__LOGIN__');
"""
    + _LAYOUT
    + r"""
$Kernel::OM->Get('Kernel::Output::HTML::Layout');
my $Guard = Kernel::Modules::TiqoraITSMReadOnly->new(
    UserID => $UID, Action => '__ACTION__', __EXTRA__);
print( $Guard->PreRun() // '' );
"""
)

_CUSTOMER_PAGE = (
    _PERL_HEAD
    + r"""
use Kernel::Modules::CustomerTiqoraITSM;
binmode STDOUT, ':encoding(UTF-8)';
$Kernel::OM->ObjectParamAdd('Kernel::Output::HTML::Layout' => {Lang => 'de'});
$Kernel::OM->Get('Kernel::Output::HTML::Layout');
my $Page = Kernel::Modules::CustomerTiqoraITSM->new(
    UserLogin => '__LOGIN__', UserCustomerID => '__COMPANY__');
print $Page->Content();
"""
)

_LINK = (
    _PERL_HEAD
    + r"""
use CGI;
use Kernel::Modules::AgentTiqoraITSMLink;
binmode STDOUT, ':encoding(UTF-8)';
local $ENV{REQUEST_METHOD} = '__METHOD__';
my $Config = $Kernel::OM->Get('Kernel::Config');
$Config->Set(Key => 'TiqoraITSM::Linking::Enabled', Value => __ENABLED__);
$Config->Set(Key => 'TiqoraITSM::APIKey', Value => '__KEY__');
$Config->Set(Key => 'TiqoraITSM::BaseURL', Value => '__BASEURL__');
my $UID = $Kernel::OM->Get('Kernel::System::User')->UserLookup(UserLogin => '__LOGIN__');
$Kernel::OM->ObjectParamAdd(
    'Kernel::System::Web::Request' => {WebRequest => CGI->new('__QUERY__')});
"""
    + _LAYOUT
    + r"""
$Kernel::OM->Get('Kernel::Output::HTML::Layout');
my $Module = Kernel::Modules::AgentTiqoraITSMLink->new(
    UserID => $UID, Action => 'AgentTiqoraITSMLink');
print $Module->Run();
"""
)

_TICKET = (
    _PERL_HEAD
    + r"""
my $T = $Kernel::OM->Get('Kernel::System::Ticket');
my $Action = '__ACTION__';
if ($Action eq 'create') {
    my $ID = $T->TicketCreate(
        Title => '__TITLE__', Queue => 'Raw', Lock => 'unlock', Priority => '3 normal',
        State => 'new', OwnerID => 1, UserID => 1,
    ) or die 'TicketCreate failed';
    print $ID;
}
else {
    my @IDs = $T->TicketSearch(Result => 'ARRAY', Title => '__TITLE__', UserID => 1,
        Limit => 50, ContentSearch => 'OR', StateType => ['new','open','closed','removed',
        'pending reminder','pending auto']);
    $T->TicketDelete(TicketID => $_, UserID => 1) for @IDs;
    print scalar @IDs;
}
"""
)


def _prerun(
    action: str, user_login: str = "root@localhost", subaction: str | None = None, **extra: str
) -> str:
    parts = []
    if subaction is not None:
        parts.append(f"Subaction => '{subaction}'")
    parts += [f"{k} => '{v}'" for k, v in extra.items()]
    code = (
        _PRERUN.replace("__LOGIN__", user_login)
        .replace("__TOKEN__", CHALLENGE_TOKEN)
        .replace("__ACTION__", action)
        .replace("__EXTRA__", ", ".join(parts))
    )
    return znuny_perl_eval(code)


def _customer_page(customer_login: str, company: str = COMPANY) -> str:
    return znuny_perl_eval(
        _CUSTOMER_PAGE.replace("__LOGIN__", customer_login).replace("__COMPANY__", company)
    )


def _link_call(
    ticket_id: int,
    ci_id: int,
    *,
    user_login: str = "root@localhost",
    method: str = "POST",
    enabled: bool = True,
    token: str | None = CHALLENGE_TOKEN,
    key: str = API_KEY,
    base_url: str = SINK_URL,
    link_type: str | None = None,
) -> str:
    query = f"TicketID={ticket_id};CIID={ci_id}"
    if link_type is not None:
        query += f";LinkType={link_type}"
    if token is not None:
        query += f";ChallengeToken={token}"
    code = (
        _LINK.replace("__METHOD__", method)
        .replace("__ENABLED__", "1" if enabled else "0")
        .replace("__KEY__", key)
        .replace("__BASEURL__", base_url)
        .replace("__LOGIN__", user_login)
        .replace("__TOKEN__", CHALLENGE_TOKEN)
        .replace("__QUERY__", query)
    )
    return znuny_perl_eval(code)


class _Seed:
    """Ids the module fixture created (CIs by role) for the assertions."""

    ci_open = 0
    ci_secret = 0
    ci_html = 0
    users_group = 0
    secret_group = 0


@pytest.fixture(scope="module")
def _engine(golden_db_url: str) -> Generator[Engine, None, None]:
    engine = create_engine(golden_db_url)
    yield engine
    engine.dispose()


def _tables() -> list[Any]:
    from tiqora.db.tiqora.models import (
        TiqoraCmdbCatalog,
        TiqoraCmdbCi,
        TiqoraCmdbClass,
        TiqoraItsmLink,
        TiqoraSettings,
    )
    from tiqora.itsm.models import TiqoraItsmLinkType

    # creation order: parents first
    return [
        TiqoraSettings.__table__,
        TiqoraItsmLinkType.__table__,
        TiqoraCmdbCatalog.__table__,
        TiqoraCmdbClass.__table__,
        TiqoraCmdbCi.__table__,
        TiqoraItsmLink.__table__,
    ]


def _seed_rows(engine: Engine, ids: dict[str, int]) -> None:
    from tiqora.db.tiqora.models import (
        TiqoraCmdbCatalog,
        TiqoraCmdbCi,
        TiqoraCmdbClass,
        TiqoraItsmLink,
    )
    from tiqora.itsm.models import TiqoraItsmLinkType

    now = datetime(2026, 10, 13, 8, 0, 0)
    with Session(engine) as session:
        if (
            session.scalar(
                select(TiqoraItsmLinkType.id).where(TiqoraItsmLinkType.name == "RelevantTo")
            )
            is None
        ):
            session.add(
                TiqoraItsmLinkType(
                    name="RelevantTo",
                    origin="znuny",
                    pointed=True,
                    propagation="none",
                    offer_ticket_ci=True,
                    create_time=now,
                    create_by=1,
                    change_time=now,
                    change_by=1,
                )
            )
        states = {}
        for key, name, functionality, klass in (
            ("depl-prod", "Produktiv", "operational", "ITSM::ConfigItem::DeploymentState"),
            ("inci-ok", "Betriebsbereit", "operational", "ITSM::Core::IncidentState"),
            ("inci-warn", "Warnung", "warning", "ITSM::Core::IncidentState"),
        ):
            row = TiqoraCmdbCatalog(
                catalog_class=klass,
                item_key=f"{PREFIX}-{key}",
                name=name,
                functionality=functionality,
                created_at=now,
                changed_at=now,
            )
            session.add(row)
            states[key] = row
        classes = {}
        for key, group, portal in (("open", ids["users"], True), ("geheim", ids["secret"], False)):
            cls = TiqoraCmdbClass(
                class_key=f"{PREFIX}-{key}",
                name="Switch" if key == "open" else "Server (geheim)",
                number_prefix=f"{PREFIX}{key[0]}",
                group_id=group,
                portal_visible=portal,
                created_by=1,
                created_at=now,
                changed_by=1,
                changed_at=now,
            )
            session.add(cls)
            classes[key] = cls
        session.flush()
        cis = {}
        for key, cls, name, inci in (
            ("open", "open", "itim56 Switch Etage 3", "inci-ok"),
            ("secret", "geheim", "itim56 Geheimer Server", "inci-warn"),
            ("html", "open", "itim56 <b>Patchfeld</b>", "inci-ok"),
            ("printer", "open", "itim56 Drucker 2. OG", "inci-ok"),
            ("mapped", "open", "itim56 Router Keller", "inci-ok"),
        ):
            ci = TiqoraCmdbCi(
                number=f"{PREFIX}-{key}",
                class_id=classes[cls].id,
                name=name,
                deployment_state_id=states["depl-prod"].id,
                incident_state_id=states[inci].id,
                cur_incident_state_id=states[inci].id,
                created_by=1,
                created_at=now,
                changed_by=1,
                changed_at=now,
            )
            session.add(ci)
            cis[key] = ci
        session.flush()
        for ci in cis.values():
            session.add(
                TiqoraItsmLink(
                    source_kind="ticket",
                    source_key=str(TICKET_ID),
                    target_kind="ci",
                    target_key=str(ci.id),
                    link_type="RelevantTo",
                    created_by=1,
                    created_at=now,
                )
            )
        # the customer user is linked to a portal and a non-portal CI, their company to a printer
        # and the company reachable only through customer_user_customer to a router
        for key, kind, target in (
            ("open", "customer_user", CUSTOMER_LOGIN),
            ("secret", "customer_user", CUSTOMER_LOGIN),
            ("printer", "customer_company", COMPANY),
            ("mapped", "customer_company", COMPANY_MAPPED),
        ):
            session.add(
                TiqoraItsmLink(
                    source_kind="ci",
                    source_key=str(cis[key].id),
                    target_kind=kind,
                    target_key=target,
                    link_type="RelevantTo",
                    created_by=1,
                    created_at=now,
                )
            )
        session.commit()
        ids.update({f"ci_{k}": int(v.id) for k, v in cis.items()})


def _unseed_rows(engine: Engine) -> None:
    from tiqora.db.tiqora.models import (
        TiqoraCmdbCatalog,
        TiqoraCmdbCi,
        TiqoraCmdbClass,
        TiqoraItsmLink,
    )

    with Session(engine) as session:
        ci_ids = [
            str(i)
            for i in session.scalars(
                select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.number.like(f"{PREFIX}-%"))
            )
        ]
        if ci_ids:
            session.execute(delete(TiqoraItsmLink).where(TiqoraItsmLink.source_key.in_(ci_ids)))
        session.execute(
            delete(TiqoraItsmLink).where(
                TiqoraItsmLink.source_kind == "ticket", TiqoraItsmLink.source_key == str(TICKET_ID)
            )
        )
        session.execute(delete(TiqoraCmdbCi).where(TiqoraCmdbCi.number.like(f"{PREFIX}-%")))
        session.execute(
            delete(TiqoraCmdbClass).where(TiqoraCmdbClass.class_key.like(f"{PREFIX}-%"))
        )
        session.execute(
            delete(TiqoraCmdbCatalog).where(TiqoraCmdbCatalog.item_key.like(f"{PREFIX}-%"))
        )
        session.commit()


@pytest.fixture(scope="module")
def addon_base(_engine: Engine) -> Generator[dict[str, int], None, None]:
    """Add-on files in the peer, CMDB fixtures in the DB; everything removed again at teardown."""
    from tiqora.cmdb.views import VIEW_CI, VIEW_CI_LINKS, create_cmdb_views, drop_cmdb_views

    tables = _tables()
    existing = set(inspect(_engine).get_table_names())
    created_tables = [t for t in tables if t.name not in existing]
    for table in created_tables:
        table.create(_engine, checkfirst=True)
    views_existing = set(inspect(_engine).get_view_names())
    created_views = not {VIEW_CI, VIEW_CI_LINKS} <= views_existing
    if created_views:
        with _engine.begin() as conn:
            assert create_cmdb_views(conn), "CREATE VIEW denied for the golden DB user"
    with _engine.begin() as conn:
        saved = {
            row[0]: row[1]
            for row in conn.execute(
                text("SELECT `key`, value FROM tiqora_settings WHERE `key` IN :k").bindparams(
                    bindparam("k", expanding=True)
                ),
                {"k": list(SETTING_KEYS)},
            )
        }
    ids: dict[str, int] = {}
    try:
        for rel in FILES:
            znuny_copy_in(ADDON / rel, f"{PEER_HOME}/{rel}")
        znuny_console("Maint::Config::Rebuild")
        peer = json.loads(znuny_perl_eval(_USERS))
        ids.update({"users": peer["users"], "secret": peer["secret"], "agent": peer["uid"]})
        _unseed_rows(_engine)  # leftovers of an aborted run
        znuny_perl_eval(_TICKET.replace("__ACTION__", "delete").replace("__TITLE__", TICKET_TITLE))
        ids["ticket"] = int(
            znuny_perl_eval(
                _TICKET.replace("__ACTION__", "create").replace("__TITLE__", TICKET_TITLE)
            )
        )
        _map_customer(_engine, present=True)
        _seed_rows(_engine, ids)
        yield ids
    finally:
        _unseed_rows(_engine)
        _map_customer(_engine, present=False)
        znuny_perl_eval(_TICKET.replace("__ACTION__", "delete").replace("__TITLE__", TICKET_TITLE))
        with _engine.begin() as conn:
            for key in SETTING_KEYS:
                conn.execute(text("DELETE FROM tiqora_settings WHERE `key` = :k"), {"k": key})
            for key, value in saved.items():
                conn.execute(
                    text("INSERT INTO tiqora_settings (`key`, value) VALUES (:k, :v)"),
                    {"k": key, "v": value},
                )
            # the agent and the secret group this module made
            conn.execute(
                text(
                    "DELETE FROM group_user WHERE user_id IN"
                    " (SELECT id FROM users WHERE login = :l)"
                ),
                {"l": AGENT_LOGIN},
            )
            conn.execute(
                text(
                    "DELETE FROM user_preferences WHERE user_id IN"
                    " (SELECT id FROM users WHERE login = :l)"
                ),
                {"l": AGENT_LOGIN},
            )
            conn.execute(text("DELETE FROM users WHERE login = :l"), {"l": AGENT_LOGIN})
            conn.execute(
                text(
                    "DELETE FROM group_user WHERE group_id IN"
                    " (SELECT id FROM permission_groups WHERE name = :n)"
                ),
                {"n": GROUP_SECRET},
            )
            conn.execute(text("DELETE FROM permission_groups WHERE name = :n"), {"n": GROUP_SECRET})
        if created_views:
            with _engine.begin() as conn:
                drop_cmdb_views(conn)
        for table in reversed(created_tables):
            table.drop(_engine, checkfirst=True)
        paths = ", ".join(f"'{PEER_HOME}/{rel}'" for rel in FILES)
        znuny_perl_eval(f"unlink {paths}; print 'ok';")
        znuny_console("Maint::Config::Rebuild")


@pytest.fixture()
def addon(addon_base: dict[str, int], _engine: Engine) -> Generator[dict[str, int], None, None]:
    """Fresh settings for one test: mode B, no read-only flag."""
    _set_setting(_engine, "itsm.mode", "tiqora")
    _delete_setting(_engine, "itsm.znuny_readonly")
    yield addon_base


@pytest.fixture()
def addon_without_views(addon: dict[str, int], _engine: Engine) -> Generator[None, None, None]:
    from tiqora.cmdb.views import create_cmdb_views, drop_cmdb_views

    with _engine.begin() as conn:
        drop_cmdb_views(conn)
    try:
        yield
    finally:
        with _engine.begin() as conn:
            assert create_cmdb_views(conn)


def _map_customer(engine: Engine, *, present: bool) -> None:
    """The customer_user_customer row that maps the customer user to COMPANY_MAPPED."""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM customer_user_customer WHERE user_id = :u AND customer_id = :c"),
            {"u": CUSTOMER_LOGIN, "c": COMPANY_MAPPED},
        )
        if present:
            conn.execute(
                text(
                    "INSERT INTO customer_user_customer"
                    " (user_id, customer_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :c, '2026-10-13 08:00:00', 1, '2026-10-13 08:00:00', 1)"
                ),
                {"u": CUSTOMER_LOGIN, "c": COMPANY_MAPPED},
            )


def _set_setting(engine: Engine, key: str, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tiqora_settings WHERE `key` = :k"), {"k": key})
        conn.execute(
            text("INSERT INTO tiqora_settings (`key`, value) VALUES (:k, :v)"),
            {"k": key, "v": value},
        )


def _delete_setting(engine: Engine, key: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tiqora_settings WHERE `key` = :k"), {"k": key})


def test_widget_lists_linked_cis_in_mode_b(addon: dict[str, int]) -> None:
    out = _widget_output(TICKET_ID, "root@localhost")
    assert "Betroffene CIs (Tiqora)" in out
    assert "itim56 Switch Etage 3" in out
    # the route Tiqora's CMDB registers (cross ruling R7), built from BaseURL + AgentCIPath
    assert f'href="{BASE_URL}/agent/cmdb/{addon["ci_open"]}"' in out
    assert "Produktiv" in out and "Betriebsbereit" in out
    assert "RelevantTo" in out
    assert "itim56 Geheimer Server" in out  # root is admin: sees every class
    assert "Warnung" in out


def test_widget_escapes_ci_names(addon: dict[str, int]) -> None:
    out = _widget_output(TICKET_ID, "root@localhost")
    assert "&lt;b&gt;Patchfeld&lt;/b&gt;" in out
    assert "<b>Patchfeld" not in out


def test_widget_hides_ci_of_foreign_class(addon: dict[str, int]) -> None:
    out = _widget_output(TICKET_ID, AGENT_LOGIN)  # ro on `users`, not in itim56-geheim
    assert "itim56 Switch Etage 3" in out
    assert "itim56 Geheimer Server" not in out
    assert f"/agent/cmdb/{addon['ci_secret']}" not in out


def test_widget_empty_for_unlinked_ticket(addon: dict[str, int]) -> None:
    assert _widget_output(TICKET_ID + 1, "root@localhost") == ""


def test_widget_inactive_in_mode_a(addon: dict[str, int], _engine: Engine) -> None:
    _set_setting(_engine, "itsm.mode", "znuny_readthrough")
    assert _widget_output(TICKET_ID, "root@localhost") == ""
    assert _addon_eval("print $Add->Active();") == "0"


def test_widget_inactive_when_off(addon: dict[str, int], _engine: Engine) -> None:
    _set_setting(_engine, "itsm.mode", "off")
    assert _widget_output(TICKET_ID, "root@localhost") == ""


def test_missing_mode_row_is_inactive(addon: dict[str, int], _engine: Engine) -> None:
    """Cross ruling R6: the add-on reads only the stored row; no row means inactive, whatever
    tables the peer has (it does not mirror the detection predicate)."""
    _delete_setting(_engine, "itsm.mode")
    assert _addon_eval("print $Add->Active();") == "0"
    assert _widget_output(TICKET_ID, "root@localhost") == ""
    _set_setting(_engine, "itsm.mode", "tiqora")
    assert _addon_eval("print $Add->Active();") == "1"


def test_views_missing_is_quiet(addon_without_views: None) -> None:
    assert _widget_output(TICKET_ID, "root@localhost") == ""
    assert _addon_eval("print scalar @{ $Add->TicketCIs(TicketID => 1, UserID => 1) };") == "0"


def test_readonly_follows_flag(addon: dict[str, int], _engine: Engine) -> None:
    assert _addon_eval("print $Add->ReadOnly();") == "0"
    _set_setting(_engine, "itsm.znuny_readonly", "1")
    assert _addon_eval("print $Add->ReadOnly();") == "1"
    _set_setting(_engine, "itsm.znuny_readonly", "0")
    assert _addon_eval("print $Add->ReadOnly();") == "0"


def test_ci_url_and_ticket_cis_fields(addon: dict[str, int]) -> None:
    out = _addon_eval(
        "use JSON::PP; "
        f"my $CIs = $Add->TicketCIs(TicketID => {TICKET_ID}, UserID => 1); "
        "print JSON::PP->new->canonical->encode($CIs);"
    )
    cis = {c["Number"]: c for c in json.loads(out)}
    ci = cis[f"{PREFIX}-open"]
    assert set(ci) == {
        "CIID",
        "Number",
        "Name",
        "ClassName",
        "DeplState",
        "DeplFunctionality",
        "InciState",
        "InciFunctionality",
        "LinkType",
        "URL",
    }
    assert int(ci["CIID"]) == addon["ci_open"]
    assert ci["ClassName"] == "Switch"
    assert (ci["DeplState"], ci["DeplFunctionality"]) == ("Produktiv", "operational")
    assert (ci["InciState"], ci["InciFunctionality"]) == ("Betriebsbereit", "operational")
    assert ci["LinkType"] == "RelevantTo"
    assert ci["URL"] == f"{BASE_URL}/agent/cmdb/{addon['ci_open']}"
    assert cis[f"{PREFIX}-secret"]["InciFunctionality"] == "warning"
    assert _addon_eval(f"print $Add->CIURL(CIID => {addon['ci_open']});") == ci["URL"]
    assert _addon_eval("print $Add->CIURL(CIID => '1 OR 1=1');") == ""


def test_customer_cis_only_portal_visible_classes(addon: dict[str, int]) -> None:
    out = _addon_eval(
        "use JSON::PP; "
        f"my $CIs = $Add->CustomerCIs(CustomerUserID => '{CUSTOMER_LOGIN}', "
        f"CustomerID => '{COMPANY}'); "
        "print JSON::PP->new->canonical->encode($CIs);"
    )
    cis = {c["Number"]: c for c in json.loads(out)}
    # user link (open), company link (printer), customer_user_customer mapping (mapped);
    # the secret CI's class is not portal-visible
    assert sorted(cis) == [f"{PREFIX}-mapped", f"{PREFIX}-open", f"{PREFIX}-printer"]
    assert cis[f"{PREFIX}-open"]["URL"] == f"{BASE_URL}/portal/config-items/{addon['ci_open']}"
    assert all("Attributes" not in ci for ci in cis.values())


def test_readonly_blocks_ci_edit_screen(addon: dict[str, int], _engine: Engine) -> None:
    _set_setting(_engine, "itsm.znuny_readonly", "1")
    for action in (
        "AgentITSMConfigItemAdd",
        "AgentITSMConfigItemEdit",
        "AgentITSMConfigItemDelete",
        "AgentITSMConfigItemBulk",
    ):
        out = _prerun(action)
        assert "Die CMDB wird jetzt in Tiqora gepflegt" in out, action
        assert BASE_URL in out  # the comment points to Tiqora


def test_readonly_blocks_class_definition_editor_only(
    addon: dict[str, int], _engine: Engine
) -> None:
    _set_setting(_engine, "itsm.znuny_readonly", "1")
    assert "Die CMDB wird jetzt in Tiqora gepflegt" in _prerun(
        "AdminITSMConfigItem", subaction="DefinitionChange"
    )
    assert "Die CMDB wird jetzt in Tiqora gepflegt" in _prerun(
        "AdminITSMConfigItem", subaction="DefinitionSave"
    )
    assert _prerun("AdminITSMConfigItem", subaction="DefinitionList") == ""
    assert _prerun("AdminITSMConfigItem", subaction="DefinitionView") == ""


def test_readonly_lets_zoom_through(addon: dict[str, int], _engine: Engine) -> None:
    _set_setting(_engine, "itsm.znuny_readonly", "1")
    for action in (
        "AgentITSMConfigItemZoom",
        "AgentITSMConfigItemSearch",
        "AgentITSMConfigItemHistory",
        "AgentITSMConfigItemPrint",
        "AgentITSMConfigItemAttachment",
        "AgentITSMConfigItem",
        "AgentTicketZoom",
    ):
        assert _prerun(action) == "", action


def test_readonly_off_lets_edit_through(addon: dict[str, int], _engine: Engine) -> None:
    _set_setting(_engine, "itsm.znuny_readonly", "0")
    assert _prerun("AgentITSMConfigItemEdit") == ""


def test_readonly_force_setting_blocks_without_flag(addon: dict[str, int]) -> None:
    forced = (
        _PRERUN.replace("__LOGIN__", "root@localhost")
        .replace("__TOKEN__", CHALLENGE_TOKEN)
        .replace("__ACTION__", "AgentITSMConfigItemEdit")
        .replace("__EXTRA__", "")
        .replace(
            "my $Guard =",
            "$Kernel::OM->Get('Kernel::Config')->Set("
            "Key => 'TiqoraITSM::ReadOnly::Force', Value => 1); my $Guard =",
        )
    )
    assert "Die CMDB wird jetzt in Tiqora gepflegt" in znuny_perl_eval(forced)


def test_readonly_blocks_linking_to_cis_only(addon: dict[str, int], _engine: Engine) -> None:
    """AgentLinkObject serves tickets, services and CIs; only CI ends are refused."""
    _set_setting(_engine, "itsm.znuny_readonly", "1")
    blocked = "Die CMDB wird jetzt in Tiqora gepflegt"
    assert blocked in _prerun(
        "AgentLinkObject", SourceObject="Ticket", TargetIdentifier="ITSMConfigItem::3"
    )
    assert blocked in _prerun(
        "AgentLinkObject", SourceObject="ITSMConfigItem", TargetIdentifier="Ticket"
    )
    assert _prerun("AgentLinkObject", SourceObject="Ticket", TargetIdentifier="Ticket") == ""
    assert _prerun("AgentLinkObject", SourceObject="Ticket", TargetIdentifier="Service") == ""


def test_customer_page_lists_own_and_company_cis(addon: dict[str, int]) -> None:
    out = _customer_page(CUSTOMER_LOGIN)
    assert "itim56 Switch Etage 3" in out  # user link
    assert "itim56 Drucker 2. OG" in out  # company link (primary CustomerID)
    assert "itim56 Router Keller" in out  # company link through customer_user_customer
    assert "itim56 Geheimer Server" not in out  # class not portal-visible
    assert f'href="{BASE_URL}/portal/config-items/{addon["ci_open"]}"' in out


def test_customer_page_escapes_and_is_empty_for_strangers(addon: dict[str, int]) -> None:
    out = _customer_page("fremd@example.org", company="itim56-fremd")
    assert "itim56 Switch Etage 3" not in out
    assert "Ihnen sind keine Konfigurationselemente zugeordnet." in out


def test_customer_page_inactive_in_mode_a(addon: dict[str, int], _engine: Engine) -> None:
    _set_setting(_engine, "itsm.mode", "znuny_readthrough")
    assert "itim56 Switch Etage 3" not in _customer_page(CUSTOMER_LOGIN)


def test_link_module_refuses_when_disabled(addon: dict[str, int]) -> None:
    gisink_clear()
    out = _link_call(addon["ticket"], addon["ci_open"], enabled=False)
    assert '"Success":0' in out
    assert gisink_requests() == []


def test_link_module_refuses_get(addon: dict[str, int]) -> None:
    gisink_clear()
    out = _link_call(addon["ticket"], addon["ci_open"], method="GET")
    assert '"Success":0' in out
    assert gisink_requests() == []


def test_link_module_refuses_missing_challenge_token(addon: dict[str, int]) -> None:
    """A state-changing AJAX action checks the challenge token itself (finding F20)."""
    gisink_clear()
    try:
        out = _link_call(addon["ticket"], addon["ci_open"], token=None)
    except RuntimeError as exc:  # FatalError may die while rendering in the bare test layout
        out = str(exc)
    assert "challenge" in out.lower()  # Znuny's own refusal page, not our JSON
    assert '"Success"' not in out
    assert gisink_requests() == []


def test_link_module_refuses_agent_without_ticket_rw(addon: dict[str, int]) -> None:
    gisink_clear()
    out = _link_call(addon["ticket"], addon["ci_open"], user_login=AGENT_LOGIN)
    assert '"Success":0' in out
    assert gisink_requests() == []


def test_link_module_refuses_bad_ids(addon: dict[str, int]) -> None:
    gisink_clear()
    out = _link_call(addon["ticket"], "1 OR 1=1")  # type: ignore[arg-type]
    assert '"Success":0' in out
    assert gisink_requests() == []


def test_link_module_posts_with_bearer_key(addon: dict[str, int]) -> None:
    gisink_clear()
    out = _link_call(addon["ticket"], addon["ci_open"])
    assert '"Success":1' in out
    assert API_KEY not in out
    req = gisink_requests()[-1]
    assert req["method"] == "POST"
    assert req["path"] == "/api/v1/cmdb/links"
    assert req["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert req["headers"]["Content-Type"].startswith("application/json")
    # cmdb's LinkIn, no note (cmdb F5)
    assert json.loads(req["body"]) == {
        "source_kind": "ci",
        "source_key": str(addon["ci_open"]),
        "target_kind": "ticket",
        "target_key": str(addon["ticket"]),
        "link_type": "RelevantTo",
    }


def test_link_module_unreachable_tiqora_does_not_leak_key(addon: dict[str, int]) -> None:
    out = _link_call(addon["ticket"], addon["ci_open"], base_url="http://127.0.0.1:9")
    assert '"Success":0' in out
    assert API_KEY not in out


def test_link_module_refuses_ci_of_foreign_class(addon: dict[str, int]) -> None:
    """root is admin and may link anything; a CI the agent cannot read is refused first."""
    gisink_clear()
    gisink_respond('{"id": 1}')
    ok = _link_call(addon["ticket"], addon["ci_secret"])  # admin reads every class
    assert '"Success":1' in ok
    gisink_clear()
    out = _link_call(addon["ticket"], 987654321)  # unknown CI
    assert '"Success":0' in out
    assert gisink_requests() == []


def _widget_with_linking(ticket_id: int, user_login: str, *, key: str = API_KEY) -> str:
    code = (
        _WIDGET.replace("__LOGIN__", user_login)
        .replace("__TICKET__", str(ticket_id))
        .replace(
            "my $UID =",
            "my $C = $Kernel::OM->Get('Kernel::Config'); "
            "$C->Set(Key => 'TiqoraITSM::Linking::Enabled', Value => 1); "
            f"$C->Set(Key => 'TiqoraITSM::APIKey', Value => '{key}'); my $UID =",
            1,
        )
    )
    return znuny_perl_eval(code)


def test_widget_offers_link_form_with_rw_on_the_ticket(addon: dict[str, int]) -> None:
    # a ticket without linked CIs still shows the widget when the agent may link
    out = _widget_with_linking(addon["ticket"], "root@localhost")
    assert 'id="TiqoraITSMLinkForm"' in out
    assert f'name="TicketID" value="{addon["ticket"]}"' in out
    assert API_KEY not in out
    # no rw on the ticket (agent has ro only) -> nothing
    assert _widget_with_linking(addon["ticket"], AGENT_LOGIN) == ""
    # no key -> nothing
    assert _widget_with_linking(addon["ticket"], "root@localhost", key="") == ""
    # linking off (default): no form
    assert "TiqoraITSMLinkForm" not in _widget_output(addon["ticket"], "root@localhost")
