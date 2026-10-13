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
  only, header fields only.

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
from _helpers import znuny_console, znuny_copy_in, znuny_perl_eval
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
        for key in ("open", "secret"):  # the customer is linked to a portal and a non-portal CI
            session.add(
                TiqoraItsmLink(
                    source_kind="ci",
                    source_key=str(cis[key].id),
                    target_kind="customer_user",
                    target_key=CUSTOMER_LOGIN,
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
        _seed_rows(_engine, ids)
        yield ids
    finally:
        _unseed_rows(_engine)
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
        "CustomerID => 'itim56-none'); "
        "print JSON::PP->new->canonical->encode($CIs);"
    )
    cis = json.loads(out)
    assert [c["Number"] for c in cis] == [f"{PREFIX}-open"]  # the secret CI's class is not visible
    assert cis[0]["URL"] == f"{BASE_URL}/portal/config-items/{addon['ci_open']}"
    assert "Attributes" not in cis[0]
