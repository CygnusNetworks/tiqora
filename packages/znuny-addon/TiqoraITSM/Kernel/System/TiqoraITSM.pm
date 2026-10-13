# --
# Copyright (C) 2026 Cygnus Networks GmbH, https://cygnusnetworks.de/
# --
# This add-on runs inside Znuny and uses Znuny's GPL-3.0-licensed APIs;
# it is therefore licensed under GPL-3.0-only (unlike the rest of the
# Tiqora project, which is AGPL-3.0-only). This software comes with
# ABSOLUTELY NO WARRANTY. For details, see the enclosed LICENSE file.
# If you did not receive it, see https://www.gnu.org/licenses/gpl-3.0.txt.
# --

package Kernel::System::TiqoraITSM;

use strict;
use warnings;

our @ObjectDependencies = (
    'Kernel::Config',
    'Kernel::System::DB',
    'Kernel::System::Group',
    'Kernel::System::Log',
);

# The most CIs one ticket (or customer) lists; the rest is only in Tiqora.
use constant MAX_CIS => 100;

=head1 NAME

Kernel::System::TiqoraITSM - Read-only access to the Tiqora CMDB from Znuny

=head1 DESCRIPTION

Once the ITSM data has moved to Tiqora ("Mode B"), the configuration items no
longer live in Znuny's C<configitem> tables. This module lets Znuny's agent
and customer screens show the CIs that belong to a ticket or a customer. It
B<only reads>, and only from the two versioned views Tiqora publishes for it:

=over 4

=item * C<tiqora_v1_itsm_ci> -- one row per CI (number, name, class, states,
the class's Znuny group, whether the class is visible in the customer portal)

=item * C<tiqora_v1_itsm_ci_links> -- one row per (CI, linked object) pair

=back

and from two C<tiqora_settings> rows: C<itsm.mode> (the add-on is active only
when it is C<tiqora>) and C<itsm.znuny_readonly> (C<1> after the switch).

Every database access uses bound parameters, is wrapped in C<eval> and never
dies: a missing view or table means "nothing to show" and is logged at
C<debug>, never as an error in the Znuny log.

=cut

=head2 new()

    my $TiqoraITSMObject = $Kernel::OM->Get('Kernel::System::TiqoraITSM');

=cut

sub new {
    my ( $Type, %Param ) = @_;

    my $Self = {};
    bless( $Self, $Type );

    return $Self;
}

=head2 Active()

C<1> when Tiqora holds the CIs (the stored C<itsm.mode> is C<tiqora>), else C<0>.
A missing row (or missing table) means inactive: the add-on never guesses.

=cut

sub Active {
    my ( $Self, %Param ) = @_;

    my $Mode = $Self->_SettingGet( Key => 'itsm.mode' );
    return 0 if !defined $Mode;

    $Mode =~ s/^\s+|\s+$//g;
    $Mode =~ s/^"(.*)"$/$1/;    # tolerate a JSON-encoded string

    return $Mode eq 'tiqora' ? 1 : 0;
}

=head2 ReadOnly()

C<1> when Znuny's own CI screens must refuse changes: after the switch
(C<itsm.znuny_readonly> is C<1>) or when the SysConfig setting
C<TiqoraITSM::ReadOnly::Force> is on.

=cut

sub ReadOnly {
    my ( $Self, %Param ) = @_;

    return 1 if $Kernel::OM->Get('Kernel::Config')->Get('TiqoraITSM::ReadOnly::Force');

    my $Flag = $Self->_SettingGet( Key => 'itsm.znuny_readonly' );
    return 0 if !defined $Flag;

    $Flag =~ s/^\s+|\s+$//g;
    $Flag =~ s/^"(.*)"$/$1/;

    return $Flag eq '1' ? 1 : 0;
}

=head2 TicketCIs()

The CIs linked to a ticket that the agent may see (read access to the group of
the CI's class; members of the C<admin> group see all).

    my $CIs = $TiqoraITSMObject->TicketCIs(
        TicketID => 123,
        UserID   => 1,
    );

Each entry is a hash with C<CIID>, C<Number>, C<Name>, C<ClassName>,
C<DeplState>, C<DeplFunctionality>, C<InciState>, C<InciFunctionality>,
C<LinkType> and C<URL>. Returns an empty array reference when the add-on is
inactive, the views are missing or on any database error.

=cut

sub TicketCIs {
    my ( $Self, %Param ) = @_;

    return [] if !$Param{TicketID} || $Param{TicketID} !~ m/^\d+$/;
    return [] if !$Param{UserID}   || $Param{UserID}   !~ m/^\d+$/;
    return [] if !$Self->Active() || !$Self->_ViewsPresent();

    my $Links = $Self->_LinksFor( Kind => 'ticket', Key => $Param{TicketID} );
    return [] if !@{$Links};

    my $Rows = $Self->_CIRows( CIIDs => [ map { $_->{CIID} } @{$Links} ] );

    my $Groups = $Self->_ReadableGroups( UserID => $Param{UserID} );

    my %LinkType = map { $_->{CIID} => $_->{LinkType} } reverse @{$Links};

    my @CIs;
    ROW:
    for my $Row ( @{$Rows} ) {
        next ROW if !$Groups->{All} && !$Groups->{ $Row->{ClassGroupID} };
        push @CIs, $Self->_CIPublic( Row => $Row, LinkType => $LinkType{ $Row->{CIID} } );
    }

    return \@CIs;
}

=head2 CustomerCIs()

The CIs a customer may see in Znuny's customer interface: CIs whose class is
visible in the customer portal and that are linked to the customer user or to
one of the customer's companies (the primary C<CustomerID> and the
C<customer_user_customer> mappings). Header fields only, no attributes.

    my $CIs = $TiqoraITSMObject->CustomerCIs(
        CustomerUserID => 'customer@example.org',
        CustomerID     => 'example-org',
    );

=cut

sub CustomerCIs {
    my ( $Self, %Param ) = @_;

    return [] if !$Self->Active() || !$Self->_ViewsPresent();

    my @Links;

    if ( defined $Param{CustomerUserID} && length $Param{CustomerUserID} ) {
        push @Links, @{ $Self->_LinksFor( Kind => 'customer_user', Key => $Param{CustomerUserID} ) };
    }

    my %Companies;
    $Companies{ $Param{CustomerID} } = 1 if defined $Param{CustomerID} && length $Param{CustomerID};
    if ( defined $Param{CustomerUserID} && length $Param{CustomerUserID} ) {
        $Companies{$_} = 1 for @{ $Self->_CustomerMappings( CustomerUserID => $Param{CustomerUserID} ) };
    }
    for my $Company ( sort keys %Companies ) {
        push @Links, @{ $Self->_LinksFor( Kind => 'customer_company', Key => $Company ) };
    }
    return [] if !@Links;

    my $Rows = $Self->_CIRows( CIIDs => [ map { $_->{CIID} } @Links ] );

    my %LinkType = map { $_->{CIID} => $_->{LinkType} } reverse @Links;

    my @CIs;
    ROW:
    for my $Row ( @{$Rows} ) {
        next ROW if !$Row->{ClassPortalVisible};
        push @CIs, $Self->_CIPublic( Row => $Row, LinkType => $LinkType{ $Row->{CIID} }, Customer => 1 );
    }

    return \@CIs;
}

=head2 CIURL()

The address of a CI in Tiqora's agent interface:
C<TiqoraITSM::BaseURL> + C<TiqoraITSM::AgentCIPath> + CI id. Empty when the
base URL is not an C<http(s)> address.

=cut

sub CIURL {
    my ( $Self, %Param ) = @_;

    return $Self->_URL( Setting => 'TiqoraITSM::AgentCIPath', CIID => $Param{CIID} );
}

sub _URL {
    my ( $Self, %Param ) = @_;

    return '' if !defined $Param{CIID} || $Param{CIID} !~ m/^\d+$/;

    my $ConfigObject = $Kernel::OM->Get('Kernel::Config');
    my $Base         = $ConfigObject->Get('TiqoraITSM::BaseURL') // '';
    my $Path         = $ConfigObject->Get( $Param{Setting} )       // '';

    $Base =~ s/^\s+|\s+$//g;
    return '' if $Base !~ m{^https?://[^\s<>"']+$}i;

    $Base =~ s{/+$}{};
    $Path = "/$Path" if $Path !~ m{^/};
    $Path .= '/'     if $Path !~ m{/$};

    return $Base . $Path . $Param{CIID};
}

=head2 _SettingGet()

The raw value of a C<tiqora_settings> row, or C<undef> when the row or the
table does not exist. Never dies.

    my $Mode = $Self->_SettingGet( Key => 'itsm.mode' );

=cut

sub _SettingGet {
    my ( $Self, %Param ) = @_;

    my $DBObject = $Kernel::OM->Get('Kernel::System::DB');
    my $Column   = $Self->_KeyColumn();
    my $Key      = $Param{Key};

    my $Value;
    my $Success = eval {
        $DBObject->Prepare(
            SQL   => "SELECT value FROM tiqora_settings WHERE $Column = ?",
            Bind  => [ \$Key ],
            Limit => 1,
        ) or die 'prepare failed';
        my @Row = $DBObject->FetchrowArray();
        $Value = $Row[0] if @Row;
        1;
    };
    if ( !$Success ) {
        $Kernel::OM->Get('Kernel::System::Log')->Log(
            Priority => 'debug',
            Message  => "TiqoraITSM: could not read tiqora_settings $Key.",
        );
        return;
    }

    return $Value;
}

=head2 _KeyColumn()

The C<tiqora_settings.key> column quoted for the database in use. C<key> is a
reserved word in MySQL/MariaDB (backticks) and needs double quotes in
PostgreSQL.

=cut

sub _KeyColumn {
    my ( $Self, %Param ) = @_;

    my $Type = eval { $Kernel::OM->Get('Kernel::System::DB')->GetDatabaseFunction('Type') } // 'mysql';

    return '"key"' if $Type eq 'postgresql';
    return '`key`';
}

=head2 _ViewsPresent()

True when both Tiqora views can be selected from (cached per object, that is
per request). Never dies.

=cut

sub _ViewsPresent {
    my ( $Self, %Param ) = @_;

    return $Self->{ViewsPresent} if defined $Self->{ViewsPresent};

    my $DBObject = $Kernel::OM->Get('Kernel::System::DB');

    my $Present = 1;
    VIEW:
    for my $View (qw(tiqora_v1_itsm_ci tiqora_v1_itsm_ci_links)) {
        my $Success = eval {
            $DBObject->Prepare(
                SQL   => "SELECT 1 FROM $View",
                Limit => 1,
            ) or die 'prepare failed';
            $DBObject->FetchrowArray();
            1;
        };
        if ( !$Success ) {
            $Kernel::OM->Get('Kernel::System::Log')->Log(
                Priority => 'debug',
                Message  => "TiqoraITSM: view $View is not available yet.",
            );
            $Present = 0;
            last VIEW;
        }
    }

    return $Self->{ViewsPresent} = $Present;
}

# (Kind, Key) -> [ { CIID, LinkType }, ... ] (sorted by CI id, at most MAX_CIS distinct CIs)
sub _LinksFor {
    my ( $Self, %Param ) = @_;

    my $DBObject = $Kernel::OM->Get('Kernel::System::DB');
    my ( $Kind, $Key ) = ( $Param{Kind}, $Param{Key} . '' );

    my @Links;
    my $Success = eval {
        $DBObject->Prepare(
            SQL => 'SELECT ci_id, link_type FROM tiqora_v1_itsm_ci_links '
                . 'WHERE object_kind = ? AND object_key = ? ORDER BY ci_id, link_type',
            Bind  => [ \$Kind, \$Key ],
            Limit => MAX_CIS * 4,
        ) or die 'prepare failed';
        while ( my @Row = $DBObject->FetchrowArray() ) {
            push @Links, { CIID => $Row[0] + 0, LinkType => $Row[1] // '' };
        }
        1;
    };
    return [] if !$Success;

    my %Seen;
    my @Distinct = grep { !$Seen{ $_->{CIID} }++ } @Links;
    splice @Distinct, MAX_CIS if @Distinct > MAX_CIS;

    return \@Distinct;
}

# [ CI ids ] -> [ { CIID, Number, ... }, ... ] ordered by number; one IN query, no join
sub _CIRows {
    my ( $Self, %Param ) = @_;

    my %Seen;
    my @IDs = grep { !$Seen{$_}++ } @{ $Param{CIIDs} // [] };
    splice @IDs, MAX_CIS if @IDs > MAX_CIS;
    return [] if !@IDs;

    my $DBObject     = $Kernel::OM->Get('Kernel::System::DB');
    my $Placeholders = join ', ', ('?') x scalar @IDs;
    my @Bind         = map { \$_ } @IDs;

    my @Rows;
    my $Success = eval {
        $DBObject->Prepare(
            SQL => 'SELECT ci_id, number, name, class_name, class_group_id, '
                . 'deployment_state, deployment_functionality, '
                . 'incident_state, incident_functionality, class_portal_visible '
                . "FROM tiqora_v1_itsm_ci WHERE ci_id IN ($Placeholders) ORDER BY number",
            Bind => \@Bind,
        ) or die 'prepare failed';
        while ( my @Row = $DBObject->FetchrowArray() ) {
            my $Portal = lc( $Row[9] // '' );
            push @Rows, {
                CIID               => $Row[0] + 0,
                Number             => $Row[1] // '',
                Name               => $Row[2] // '',
                ClassName          => $Row[3] // '',
                ClassGroupID       => $Row[4] // 0,
                DeplState          => $Row[5] // '',
                DeplFunctionality  => $Row[6] // '',
                InciState          => $Row[7] // '',
                InciFunctionality  => $Row[8] // '',
                ClassPortalVisible => ( $Portal eq '1' || $Portal eq 't' || $Portal eq 'true' ) ? 1 : 0,
            };
        }
        1;
    };
    return [] if !$Success;

    return \@Rows;
}

sub _CIPublic {
    my ( $Self, %Param ) = @_;

    my %CI = %{ $Param{Row} };
    delete $CI{ClassGroupID};
    delete $CI{ClassPortalVisible};

    $CI{LinkType} = $Param{LinkType} // '';
    $CI{URL}      = $Param{Customer}
        ? $Self->_URL( Setting => 'TiqoraITSM::CustomerCIPath', CIID => $CI{CIID} )
        : $Self->CIURL( CIID => $CI{CIID} );

    return \%CI;
}

# The customer's company ids from the customer_user_customer mapping (empty if the table is absent).
sub _CustomerMappings {
    my ( $Self, %Param ) = @_;

    my $DBObject = $Kernel::OM->Get('Kernel::System::DB');
    my $Login    = $Param{CustomerUserID};

    my @IDs;
    my $Success = eval {
        $DBObject->Prepare(
            SQL   => 'SELECT customer_id FROM customer_user_customer WHERE user_id = ?',
            Bind  => [ \$Login ],
            Limit => 100,
        ) or die 'prepare failed';
        while ( my @Row = $DBObject->FetchrowArray() ) {
            push @IDs, $Row[0] if defined $Row[0] && length $Row[0];
        }
        1;
    };

    return $Success ? \@IDs : [];
}

# { All => 1 } for admins, otherwise { GroupID => 1, ... } of the groups with ro or rw permission.
sub _ReadableGroups {
    my ( $Self, %Param ) = @_;

    my $GroupObject = $Kernel::OM->Get('Kernel::System::Group');

    my %Groups;
    eval {
        for my $Type (qw(ro rw)) {
            my %Permitted = $GroupObject->PermissionUserGet(
                UserID => $Param{UserID},
                Type   => $Type,
            );
            for my $ID ( keys %Permitted ) {
                $Groups{$ID} = 1;
                $Groups{All} = 1 if $Type eq 'rw' && $Permitted{$ID} eq 'admin';
            }
        }
        1;
    } or do {
        $Kernel::OM->Get('Kernel::System::Log')->Log(
            Priority => 'debug',
            Message  => "TiqoraITSM: could not read the groups of user $Param{UserID}.",
        );
        return {};
    };

    return \%Groups;
}

1;
