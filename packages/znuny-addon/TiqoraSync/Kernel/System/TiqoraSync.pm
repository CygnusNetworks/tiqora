# --
# Copyright (C) 2026 Cygnus Networks GmbH, https://cygnusnetworks.de/
# --
# This add-on runs inside Znuny and uses Znuny's GPL-3.0-licensed APIs;
# it is therefore licensed under GPL-3.0-only (unlike the rest of the
# Tiqora project, which is AGPL-3.0-only). This software comes with
# ABSOLUTELY NO WARRANTY. For details, see the enclosed LICENSE file.
# If you did not receive it, see https://www.gnu.org/licenses/gpl-3.0.txt.
# --

package Kernel::System::TiqoraSync;

use strict;
use warnings;

our @ObjectDependencies = (
    'Kernel::System::Cache',
    'Kernel::System::DB',
    'Kernel::System::Log',
);

=head1 NAME

Kernel::System::TiqoraSync - Ticket cache invalidation for Tiqora parallel operation

=head1 DESCRIPTION

Tiqora is a Python/FastAPI reimplementation of Znuny that runs alongside this
Znuny instance on the same MySQL/MariaDB database ("parallel operation").
Tiqora writes tickets directly via SQL, bypassing Znuny's Perl object layer,
which means Znuny's in-process object cache (L<Kernel::System::Cache>) can go
stale for tickets that Tiqora touched.

Whenever Tiqora writes a ticket, it records the affected C<TicketID> in the
hand-off table C<tiqora_cache_invalidation>. This module is invoked by a
daemon cron task (see C<Kernel::Config::Files::XML::TiqoraSync>) and:

=over 4

=item * reads the last-processed id from C<tiqora_settings> (key
C<tiqorasync.watermark>, defaulting to C<0> if unset)

=item * selects up to 500 new rows from C<tiqora_cache_invalidation>

=item * clears the Znuny ticket cache for every distinct C<TicketID> found

=item * advances the watermark to the highest id processed

=back

Both tables are created by Tiqora's own Alembic migrations and are therefore
not guaranteed to exist yet -- Znuny may start before Tiqora has been
deployed or migrated. Every database access in this module is wrapped so
that a missing table (or any other DB error) is treated as "not ready yet"
and logged at C<debug> level, never as a fatal error. A daemon cron task
that dies would otherwise show up as noisy, misleading errors in the
scheduler log.

=head1 PUBLIC INTERFACE

=cut

# Maximum number of tiqora_cache_invalidation rows processed per run. Keeps
# a single daemon cron tick bounded even if a backlog has built up; the
# remainder is picked up on the next run (every cron tick, see
# TiqoraSync.xml).
use constant BATCH_LIMIT => 500;

use constant WATERMARK_KEY => 'tiqorasync.watermark';

=head2 new()

Don't use the constructor directly, use the ObjectManager instead:

    my $TiqoraSyncObject = $Kernel::OM->Get('Kernel::System::TiqoraSync');

=cut

sub new {
    my ( $Type, %Param ) = @_;

    # allocate new hash for object
    my $Self = {};
    bless( $Self, $Type );

    return $Self;
}

=head2 Run()

Called by the daemon cron task (C<Daemon::SchedulerCronTaskManager::Task###TiqoraSync>).
Reads new rows from C<tiqora_cache_invalidation>, invalidates the Znuny
ticket cache for every affected ticket, and advances the watermark.

    my $Success = $TiqoraSyncObject->Run();

Always returns C<1> -- failures are logged and swallowed so the daemon
scheduler never sees this task as erroring out.

=cut

sub Run {
    my ( $Self, %Param ) = @_;

    my $DBObject = $Kernel::OM->Get('Kernel::System::DB');

    # Both hand-off tables are created by Tiqora's Alembic migrations, which
    # may not have run yet (or Tiqora may not be deployed at all). Treat any
    # DB error here as "not ready", not as a failure.
    if ( !$Self->_TablesExist() ) {
        $Kernel::OM->Get('Kernel::System::Log')->Log(
            Priority => 'debug',
            Message  =>
                'TiqoraSync: tiqora_cache_invalidation / tiqora_settings not present yet, skipping this run.',
        );
        return 1;
    }

    my $Watermark = $Self->_WatermarkGet();

    my $SelectSuccess = eval {
        $DBObject->Prepare(
            SQL => 'SELECT ticket_id, id FROM tiqora_cache_invalidation '
                . 'WHERE id > ? ORDER BY id ASC LIMIT ' . BATCH_LIMIT,
            Bind => [ \$Watermark ],
        );
    };

    if ( !$SelectSuccess || $@ ) {
        $Kernel::OM->Get('Kernel::System::Log')->Log(
            Priority => 'notice',
            Message  => "TiqoraSync: could not query tiqora_cache_invalidation, skipping this run ($@).",
        );
        return 1;
    }

    my %TicketIDs;
    my $MaxID = $Watermark;

    while ( my @Row = $DBObject->FetchrowArray() ) {
        my ( $TicketID, $ID ) = @Row;
        $TicketIDs{$TicketID} = 1;
        $MaxID = $ID if $ID > $MaxID;
    }

    # Nothing new to process.
    return 1 if $MaxID == $Watermark;

    for my $TicketID ( sort keys %TicketIDs ) {
        $Self->_TicketCacheInvalidate( TicketID => $TicketID );
    }

    # Coarse fallback, done once per run: also drop the entire Ticket cache
    # type so any list- or count-level cache entries derived from these
    # tickets (which we can not enumerate exhaustively per-ticket) can not
    # remain stale.
    $Kernel::OM->Get('Kernel::System::Cache')->CleanUp( Type => 'Ticket' );

    $Self->_WatermarkSet( Watermark => $MaxID );

    return 1;
}

=head2 _TablesExist()

Checks (defensively) whether both hand-off tables Tiqora relies on already
exist. Returns a true/false value, never dies.

=cut

sub _TablesExist {
    my ( $Self, %Param ) = @_;

    my $DBObject = $Kernel::OM->Get('Kernel::System::DB');

    for my $Table (qw(tiqora_cache_invalidation tiqora_settings)) {
        my $Success = eval {
            $DBObject->Prepare(
                SQL   => 'SHOW TABLES LIKE ?',
                Bind  => [ \$Table ],
                Limit => 1,
            );
        };
        return 0 if !$Success || $@;

        my @Row = eval { $DBObject->FetchrowArray() };
        return 0 if !@Row;
    }

    return 1;
}

=head2 _WatermarkGet()

Reads the last-processed C<tiqora_cache_invalidation.id> from
C<tiqora_settings>. Returns C<0> if the row does not exist yet or on any DB
error.

=cut

sub _WatermarkGet {
    my ( $Self, %Param ) = @_;

    my $DBObject = $Kernel::OM->Get('Kernel::System::DB');

    my $Success = eval {
        $DBObject->Prepare(
            SQL   => 'SELECT value FROM tiqora_settings WHERE `key` = ?',
            Bind  => [ \WATERMARK_KEY ],
            Limit => 1,
        );
    };
    return 0 if !$Success || $@;

    my @Row = eval { $DBObject->FetchrowArray() };
    return 0 if !@Row || !defined $Row[0];

    return $Row[0] + 0;
}

=head2 _WatermarkSet()

Persists the given watermark value to C<tiqora_settings> via an UPSERT.

    $Self->_WatermarkSet( Watermark => 123 );

=cut

sub _WatermarkSet {
    my ( $Self, %Param ) = @_;

    my $DBObject = $Kernel::OM->Get('Kernel::System::DB');

    eval {
        $DBObject->Do(
            SQL => 'INSERT INTO tiqora_settings (`key`, value) VALUES (?, ?) '
                . 'ON DUPLICATE KEY UPDATE value = VALUES(value)',
            Bind => [ \WATERMARK_KEY, \$Param{Watermark} ],
        );
    };
    if ($@) {
        $Kernel::OM->Get('Kernel::System::Log')->Log(
            Priority => 'notice',
            Message  => "TiqoraSync: could not update watermark in tiqora_settings ($@).",
        );
    }

    return 1;
}

=head2 _TicketCacheInvalidate()

Invalidates Znuny's per-ticket cache entries for the given TicketID. Mirrors
what L<Kernel::System::Ticket>'s private C<_TicketCacheClear()> does for its
most important keys (C<Cache::GetTicket$TicketID> and its "extended"/
dynamic-fields variants, all under C<CacheType =E<gt> 'Ticket'>). Any
list-/count-level cache entries that can not be enumerated exhaustively here
are handled by a single, coarser C<CleanUp( Type =E<gt> 'Ticket' )> call in
L</Run()> after all tickets in a batch have been processed.

    $Self->_TicketCacheInvalidate( TicketID => 123 );

=cut

sub _TicketCacheInvalidate {
    my ( $Self, %Param ) = @_;

    return if !$Param{TicketID};

    my $CacheObject = $Kernel::OM->Get('Kernel::System::Cache');

    $CacheObject->Delete(
        Type => 'Ticket',
        Key  => 'Cache::GetTicket' . $Param{TicketID},
    );

    for my $Extended ( 0 .. 1 ) {
        for my $FetchDynamicFields ( 0 .. 1 ) {
            $CacheObject->Delete(
                Type => 'Ticket',
                Key  => 'Cache::GetTicket' . $Param{TicketID} . '::' . $Extended . '::' . $FetchDynamicFields,
            );
        }
    }

    return 1;
}

1;

=head1 LICENSE

This software is licensed under the GNU Affero General Public License
version 3 (AGPL-3.0-only). See the enclosed LICENSE file for details.

=cut
