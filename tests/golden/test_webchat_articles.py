"""Golden-master: a WebChat article written by Tiqora is a normal Znuny article.

Tiqora registers the ``WebChat`` communication channel (module
``Kernel::System::CommunicationChannel::Internal``, package ``Tiqora``) and
writes chat lines as plain MIME articles on it. Real Znuny must resolve the
article backend (the Internal one, not ``…::Invalid``), read body and
attachment, survive ``Admin::CommunicationChannel::Sync`` and delete the whole
ticket again.
"""

from __future__ import annotations

import pytest
from _helpers import znuny_console, znuny_perl_eval
from sqlalchemy import text

pytestmark = pytest.mark.golden

_READ_PERL = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-master'},
);
my $ArticleObject = $Kernel::OM->Get('Kernel::System::Ticket::Article');
my $Backend = $ArticleObject->BackendForArticle(TicketID => %(tid)d, ArticleID => %(aid)d);
my %%Article = $Backend->ArticleGet(TicketID => %(tid)d, ArticleID => %(aid)d);
my %%Index = $Backend->ArticleAttachmentIndex(ArticleID => %(aid)d, UserID => 1);
print 'backend=', ref($Backend), "\n";
print 'body=', $Article{Body}, "\n";
print 'files=', join(',', sort map { $_->{Filename} } values %%Index), "\n";
"""

_DELETE_PERL = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-master'},
);
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $Result = $TicketObject->TicketDelete(TicketID => %(tid)d, UserID => 1);
print 'deleted=', ($Result ? 1 : 0), "\n";
"""


def _parse(out: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


async def _scalar(session, sql: str, **params):  # type: ignore[no-untyped-def]
    return (await session.execute(text(sql), params)).scalar_one()


async def test_webchat_article_is_readable_by_znuny(golden_session_factory) -> None:  # type: ignore[no-untyped-def]
    from tiqora.channels.common import ensure_webchat_channel_row
    from tiqora.domain.ticket_write_service import ArticleIn, TicketIn, add_article, create_ticket
    from tiqora.znuny.sysconfig import SysConfig

    async with golden_session_factory() as session:
        queue_id = await _scalar(session, "SELECT id FROM queue WHERE name = 'Golden' LIMIT 1")
        state_id = await _scalar(session, "SELECT id FROM ticket_state WHERE name = 'new' LIMIT 1")
        prio_id = await _scalar(
            session, "SELECT id FROM ticket_priority WHERE name = '3 normal' LIMIT 1"
        )

    async with golden_session_factory() as session:
        # A row left by an earlier run would hide what Tiqora writes now.
        await session.execute(
            text(
                "DELETE FROM communication_channel WHERE name = 'WebChat' AND NOT EXISTS"
                " (SELECT 1 FROM article a WHERE a.communication_channel_id ="
                " communication_channel.id)"
            )
        )
        await ensure_webchat_channel_row(session)
        ticket_id = await create_ticket(
            session,
            golden_session_factory,
            SysConfig(session),
            params=TicketIn(
                title="Golden webchat",
                queue_id=int(queue_id),
                state_id=int(state_id),
                priority_id=int(prio_id),
                owner_id=1,
                customer_id="golden-customer",
                customer_user_id="golden.customer",
            ),
            user_id=1,
        )
        article_id = await add_article(
            session,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="customer",
                is_visible_for_customer=True,
                subject="Chat",
                body="Hallo aus dem Chat",
                from_address='"Golden Visitor" <visitor@example.org>',
                channel="webchat",
                attachments=[("hello.txt", "text/plain", b"hello attachment")],
            ),
            user_id=1,
            sysconfig=SysConfig(session),
        )
        await session.commit()

    read = _READ_PERL % {"tid": ticket_id, "aid": article_id}
    got = _parse(znuny_perl_eval(read))
    assert got["backend"] == "Kernel::System::Ticket::Article::Backend::Internal"
    assert "Invalid" not in got["backend"]
    assert got["body"] == "Hallo aus dem Chat"
    assert got["files"] == "hello.txt"

    # Sync only drops channels without an article (ChannelDrop bails otherwise):
    # the row and the article must survive and still resolve.
    znuny_console("Admin::CommunicationChannel::Sync")
    async with golden_session_factory() as session:
        assert (
            await _scalar(
                session, "SELECT COUNT(*) FROM communication_channel WHERE name = 'WebChat'"
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
