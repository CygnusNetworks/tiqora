"""Golden-master: what a plugin makes Tiqora write stays readable by real Znuny.

Two artefacts of the plugin extension points (band 55490-55499 is reserved for this module's
rows; it creates tickets only through the write service and the dynamic field only through the
admin route):

* an article on a plugin-registered channel (``Plugin_points_Chat``) lives on a communication
  channel row with Znuny's internal module, so ``BackendForArticle`` resolves the Internal
  backend (not ``…::Invalid``), body and attachment read back, and
  ``Admin::CommunicationChannel::Sync`` keeps the row;
* a plugin dynamic field type is a plain ``Text`` field for Znuny (``TiqoraPluginType`` is just
  an unknown key in its YAML config): ``DynamicFieldGet``, ``ValueGet`` and
  ``DisplayValueRender`` work.

The ``org.example.points`` fixture plugin is booted in-process the way the backend tests do it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from _helpers import znuny_console, znuny_perl_eval
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

pytestmark = pytest.mark.golden

CHANNEL_NAME = "Plugin_points_Chat"
FIELD = "PP55Vertrag"
PLUGIN_TYPE = "Plugin_points_Contract"

_PERL_HEAD = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-master'},
);
"""

_READ_PERL = (
    _PERL_HEAD
    + r"""
my $ArticleObject = $Kernel::OM->Get('Kernel::System::Ticket::Article');
my $Backend = $ArticleObject->BackendForArticle(TicketID => %(tid)d, ArticleID => %(aid)d);
my %%Article = $Backend->ArticleGet(TicketID => %(tid)d, ArticleID => %(aid)d);
my %%Index = $Backend->ArticleAttachmentIndex(ArticleID => %(aid)d, UserID => 1);
print 'backend=', ref($Backend), "\n";
print 'body=', $Article{Body}, "\n";
print 'files=', join(',', sort map { $_->{Filename} } values %%Index), "\n";
"""
)

_DF_PERL = (
    _PERL_HEAD
    + r"""
$Kernel::OM->ObjectParamAdd('Kernel::Output::HTML::Layout' => {Lang => 'en', UserID => 1});
my $DFObject = $Kernel::OM->Get('Kernel::System::DynamicField');
my $Backend = $Kernel::OM->Get('Kernel::System::DynamicField::Backend');
my $DF = $DFObject->DynamicFieldGet(Name => '%(name)s');
print 'type=', $DF->{FieldType}, "\n";
print 'object=', $DF->{ObjectType}, "\n";
print 'plugin_type=', $DF->{Config}->{TiqoraPluginType}, "\n";
my $Value = $Backend->ValueGet(DynamicFieldConfig => $DF, ObjectID => %(tid)d);
print 'value=', $Value, "\n";
my $Rendered = eval {
    $Backend->DisplayValueRender(
        DynamicFieldConfig => $DF, Value => $Value, HTMLOutput => 0,
        LayoutObject => $Kernel::OM->Get('Kernel::Output::HTML::Layout'),
    );
};
print 'render_died=', ($@ ? 1 : 0), "\n";
print 'rendered=', (ref $Rendered ? $Rendered->{Value} : ''), "\n";
"""
)

_DELETE_PERL = (
    _PERL_HEAD
    + r"""
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $Result = $TicketObject->TicketDelete(TicketID => %(tid)d, UserID => 1);
print 'deleted=', ($Result ? 1 : 0), "\n";
"""
)


def _parse(out: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


async def _scalar(session, sql: str, **params):  # type: ignore[no-untyped-def]
    return (await session.execute(text(sql), params)).scalar_one()


@pytest.fixture()
def points_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The ``org.example.points`` fixture plugin in a frozen in-process registry."""
    from tests._plugin_points import boot_points_plugin, teardown_points_plugin

    boot_points_plugin(tmp_path, monkeypatch)
    yield
    teardown_points_plugin()


async def _new_ticket(factory, title: str) -> int:  # type: ignore[no-untyped-def]
    from tiqora.domain.ticket_write_service import TicketIn, create_ticket
    from tiqora.znuny.sysconfig import SysConfig

    async with factory() as session:
        queue_id = await _scalar(session, "SELECT id FROM queue WHERE name = 'Golden' LIMIT 1")
        state_id = await _scalar(session, "SELECT id FROM ticket_state WHERE name = 'new' LIMIT 1")
        prio_id = await _scalar(
            session, "SELECT id FROM ticket_priority WHERE name = '3 normal' LIMIT 1"
        )
    async with factory() as session:
        ticket_id = await create_ticket(
            session,
            factory,
            SysConfig(session),
            params=TicketIn(
                title=title,
                queue_id=int(queue_id),
                state_id=int(state_id),
                priority_id=int(prio_id),
                owner_id=1,
                customer_id="golden-customer",
                customer_user_id="golden.customer",
            ),
            user_id=1,
        )
        await session.commit()
    return ticket_id


async def test_znuny_reads_plugin_channel_article(golden_session_factory, points_plugin) -> None:  # type: ignore[no-untyped-def]
    from tiqora.domain.ticket_write_service import ArticleIn, add_article
    from tiqora.plugins.channels import driver_for, ensure_tiqora_channel_row
    from tiqora.znuny.sysconfig import SysConfig

    assert driver_for("plugin_points_chat") is not None  # the fixture plugin registered it

    async with golden_session_factory() as session:
        # A row left by an earlier run would hide what Tiqora writes now.
        await session.execute(
            text(
                "DELETE FROM communication_channel WHERE name = :n AND NOT EXISTS"
                " (SELECT 1 FROM article a WHERE a.communication_channel_id ="
                " communication_channel.id)"
            ),
            {"n": CHANNEL_NAME},
        )
        channel_id = await ensure_tiqora_channel_row(session, CHANNEL_NAME)
        await session.commit()

    ticket_id = await _new_ticket(golden_session_factory, "Golden plugin channel")
    async with golden_session_factory() as session:
        article_id = await add_article(
            session,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="customer",
                is_visible_for_customer=True,
                subject="Chat",
                body="Hallo Welt",
                from_address="golden-visitor",
                channel="plugin_points_chat",
                attachments=[("hello.txt", "text/plain", b"hello attachment")],
            ),
            user_id=1,
            sysconfig=SysConfig(session),
        )
        await session.commit()
        assert (
            await _scalar(
                session,
                "SELECT communication_channel_id FROM article WHERE id = :a",
                a=article_id,
            )
            == channel_id
        )

    read = _READ_PERL % {"tid": ticket_id, "aid": article_id}
    got = _parse(znuny_perl_eval(read))
    assert got["backend"] == "Kernel::System::Ticket::Article::Backend::Internal"
    assert "Invalid" not in got["backend"]
    assert got["body"] == "Hallo Welt"
    assert got["files"] == "hello.txt"

    # Sync only drops channels without an article: the row and the article must survive.
    znuny_console("Admin::CommunicationChannel::Sync")
    async with golden_session_factory() as session:
        assert (
            await _scalar(
                session,
                "SELECT COUNT(*) FROM communication_channel WHERE name = :n",
                n=CHANNEL_NAME,
            )
            == 1
        )
        assert (
            await _scalar(session, "SELECT COUNT(*) FROM article WHERE id = :a", a=article_id) == 1
        )
    assert _parse(znuny_perl_eval(read))["backend"].endswith("::Internal")

    assert _parse(znuny_perl_eval(_DELETE_PERL % {"tid": ticket_id}))["deleted"] == "1"
    async with golden_session_factory() as session:
        assert (
            await _scalar(session, "SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=ticket_id)
            == 0
        )
        # drop the channel row again: it belongs to this test run
        await session.execute(
            text("DELETE FROM communication_channel WHERE name = :n"), {"n": CHANNEL_NAME}
        )
        await session.commit()


@pytest.fixture()
async def admin_client(golden_session_factory) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.api.v1.admin.deps import get_admin_user
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    root = AuthenticatedUser(
        id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
    )

    async def _db() -> Any:
        async with golden_session_factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: root
    app.dependency_overrides[get_current_user] = lambda: root
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _drop_field(factory) -> None:  # type: ignore[no-untyped-def]
    async with factory() as session:
        await session.execute(
            text(
                "DELETE FROM dynamic_field_value WHERE field_id IN"
                " (SELECT id FROM dynamic_field WHERE name = :n)"
            ),
            {"n": FIELD},
        )
        await session.execute(text("DELETE FROM dynamic_field WHERE name = :n"), {"n": FIELD})
        await session.commit()


async def test_znuny_reads_plugin_dynamic_field(  # type: ignore[no-untyped-def]
    golden_session_factory, points_plugin, admin_client
) -> None:
    from tiqora.domain.ticket_write_service import update_dynamic_field

    await _drop_field(golden_session_factory)
    ticket_id = await _new_ticket(golden_session_factory, "Golden plugin dynamic field")
    try:
        resp = await admin_client.post(
            "/api/v1/admin/dynamic-fields",
            json={
                "name": FIELD,
                "label": "Vertrag",
                "field_order": 9955,
                "field_type": "Text",
                "plugin_type": PLUGIN_TYPE,
                "config": {"TiqoraPluginConfig": {"prefix": "V-"}},
            },
        )
        assert resp.status_code == 201, resp.text

        async with golden_session_factory() as session:
            await update_dynamic_field(
                session, ticket_id=ticket_id, field_name=FIELD, values=["V-12"], user_id=1
            )
            await session.commit()

        # Tiqora's rows bypass Znuny's file cache (TiqoraSync would signal it in production).
        znuny_console("Maint::Cache::Delete")
        got = _parse(znuny_perl_eval(_DF_PERL % {"name": FIELD, "tid": ticket_id}))
        assert got["type"] == "Text"
        assert got["object"] == "Ticket"
        assert got["plugin_type"] == PLUGIN_TYPE
        assert got["value"] == "V-12"
        assert got["render_died"] == "0"
        assert "V-12" in got["rendered"]

        assert _parse(znuny_perl_eval(_DELETE_PERL % {"tid": ticket_id}))["deleted"] == "1"
    finally:
        await _drop_field(golden_session_factory)
        znuny_console("Maint::Cache::Delete")
