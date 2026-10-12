"""Golden-master: a WebChat article written by Tiqora is a normal Znuny article.

Tiqora registers the ``WebChat`` communication channel (module
``Kernel::System::CommunicationChannel::Internal``, package ``Tiqora``) and
writes chat lines as plain MIME articles on it. Real Znuny must resolve the
article backend (the Internal one, not ``…::Invalid``), read body and
attachment, survive ``Admin::CommunicationChannel::Sync`` and delete the whole
ticket again.

The chat assistant's articles are the same kind of article: a bot reply is an agent
article on the ``WebChat`` channel written by the queue's AI service user, and the
handoff summary is an internal note. Znuny shows both (band 54740-54759 is reserved
for this module's rows; it creates tickets only through the write service).
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


_BOT_READ_PERL = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-master'},
);
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $ArticleObject = $Kernel::OM->Get('Kernel::System::Ticket::Article');
my %%Ticket = $TicketObject->TicketGet(TicketID => %(tid)d, UserID => 1);
print 'ticket=', $Ticket{Title}, "\n";
my @Articles = $ArticleObject->ArticleList(TicketID => %(tid)d);
print 'count=', scalar(@Articles), "\n";
my $Index = 0;
for my $Meta (@Articles) {
    my $Backend = $ArticleObject->BackendForArticle(%%{$Meta});
    my %%Article = $Backend->ArticleGet(TicketID => %(tid)d, ArticleID => $Meta->{ArticleID});
    print "a$Index=", join('|',
        $Meta->{ArticleID}, ref($Backend), $Article{SenderType}, $Article{IsVisibleForCustomer},
        $Article{CreateBy}, $Article{CommunicationChannelID} // '', $Article{Subject},
        $Article{Body}), "\n";
    $Index++;
}
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


async def test_bot_reply_and_handoff_note_are_normal_znuny_articles(  # type: ignore[no-untyped-def]
    golden_session_factory,
) -> None:
    """R-CHANNEL-ROW d: the assistant's articles resolve, list and survive the channel sync."""
    from tiqora.channels.common import ensure_webchat_channel_row
    from tiqora.domain.ticket_write_service import ArticleIn, TicketIn, add_article, create_ticket
    from tiqora.znuny.sysconfig import SysConfig

    async with golden_session_factory() as session:
        queue_id = await _scalar(session, "SELECT id FROM queue WHERE name = 'Golden' LIMIT 1")
        state_id = await _scalar(session, "SELECT id FROM ticket_state WHERE name = 'new' LIMIT 1")
        prio_id = await _scalar(
            session, "SELECT id FROM ticket_priority WHERE name = '3 normal' LIMIT 1"
        )
        # The queue's AI service user is an ordinary agent account; the seed's agent stands in.
        service_user_id = int(
            await _scalar(session, "SELECT id FROM users WHERE login = 'golden.agent' LIMIT 1")
        )
        await ensure_webchat_channel_row(session)
        await session.commit()

    async with golden_session_factory() as session:
        ticket_id = await create_ticket(
            session,
            golden_session_factory,
            SysConfig(session),
            params=TicketIn(
                title="Golden webchat assistant",
                queue_id=int(queue_id),
                state_id=int(state_id),
                priority_id=int(prio_id),
                owner_id=1,
                customer_id="golden-customer",
                customer_user_id="golden.customer",
            ),
            user_id=1,
        )
        visitor_id = await add_article(
            session,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="customer",
                is_visible_for_customer=True,
                subject="Chat",
                body="Wie setze ich mein Passwort zurück?",
                from_address='"Golden Visitor" <visitor@example.org>',
                channel="webchat",
            ),
            user_id=1,
            sysconfig=SysConfig(session),
        )
        # What deliver_bot_chat_reply writes: an agent article by the service user.
        bot_id = await add_article(
            session,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=True,
                subject="Re: Chat",
                body="Öffnen Sie die Anmeldeseite und wählen Sie Passwort vergessen.",
                to_address='"Golden Visitor" <visitor@example.org>',
                channel="webchat",
            ),
            user_id=service_user_id,
            sysconfig=SysConfig(session),
        )
        # What hand_off_chat writes: an internal note that summarises the conversation.
        note_id = await add_article(
            session,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=False,
                subject="KI-Übergabe im Chat",
                body="Der Besucher braucht einen Menschen: Konto gesperrt.",
                channel="note",
            ),
            user_id=service_user_id,
            sysconfig=SysConfig(session),
        )
        await session.commit()

    def read() -> dict[str, str]:
        return _parse(znuny_perl_eval(_BOT_READ_PERL % {"tid": ticket_id}))

    def check(got: dict[str, str]) -> None:
        assert got["ticket"] == "Golden webchat assistant"
        assert got["count"] == "3"
        rows = {int(v.split("|")[0]): v.split("|") for k, v in got.items() if k.startswith("a")}
        assert set(rows) == {visitor_id, bot_id, note_id}
        internal = "Kernel::System::Ticket::Article::Backend::Internal"
        for row in rows.values():
            assert row[1] == internal
        # bot reply: agent sender, visible to the customer, written by the service user
        assert rows[bot_id][2] == "agent"
        assert rows[bot_id][3] == "1"
        assert rows[bot_id][4] == str(service_user_id)
        assert rows[bot_id][7].startswith("Öffnen Sie die Anmeldeseite")
        # same communication channel as the visitor's line (WebChat), not the note's channel
        assert rows[bot_id][5] == rows[visitor_id][5]
        # handoff note: agent sender, internal, own channel
        assert rows[note_id][2] == "agent"
        assert rows[note_id][3] == "0"
        assert rows[note_id][4] == str(service_user_id)
        assert rows[note_id][6] == "KI-Übergabe im Chat"
        assert rows[note_id][5] != rows[bot_id][5]

    check(read())

    znuny_console("Admin::CommunicationChannel::Sync")
    async with golden_session_factory() as session:
        assert (
            await _scalar(
                session, "SELECT COUNT(*) FROM communication_channel WHERE name = 'WebChat'"
            )
            == 1
        )
        assert (
            await _scalar(session, "SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=ticket_id)
            == 3
        )
    check(read())

    assert _parse(znuny_perl_eval(_DELETE_PERL % {"tid": ticket_id}))["deleted"] == "1"
