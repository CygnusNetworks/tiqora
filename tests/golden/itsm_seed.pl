#!/usr/bin/perl
# Seed ITSM fixtures into a golden ITSM peer (I15). Copy in with `znuny_copy_in`, run with
# `perl /tmp/itsm_seed.pl` as user otrs (see test_itsm_peer_smoke.py).
#
#   * service itsm94-svc-web (criticality "4 high", type "End User Service") + SLA
#     itsm94-sla-web with MinTimeBetweenIncidents
#   * CIs in the default classes: Computer x2 (2 and 3 versions), Network x1 (incident state)
#   * links CI->service (DependsOn), CI<->CI (DependsOn), ticket<->CI (RelevantTo)
#
# All names are *.example.org, addresses come from 192.0.2.0/24 (RFC 5737). Idempotent: when
# the service already exists nothing is created. Prints one JSON line between
# GOLDEN_ITSM_SEED_BEGIN / GOLDEN_ITSM_SEED_END.
use strict;
use warnings;
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;

local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => { LogPrefix => 'golden-itsm-seed' },
);

my $ServiceObject = $Kernel::OM->Get('Kernel::System::Service');
my $SLAObject     = $Kernel::OM->Get('Kernel::System::SLA');
my $CIObject      = $Kernel::OM->Get('Kernel::System::ITSMConfigItem');
my $LinkObject    = $Kernel::OM->Get('Kernel::System::LinkObject');
my $GCObject      = $Kernel::OM->Get('Kernel::System::GeneralCatalog');
my $TicketObject  = $Kernel::OM->Get('Kernel::System::Ticket');
my $JSON          = $Kernel::OM->Get('Kernel::System::JSON');

my $ServiceName = 'itsm94-svc-web';
my $Existing    = $ServiceObject->ServiceLookup( Name => $ServiceName );

sub _Reverse {
    my ($Class) = @_;
    my $List = $GCObject->ItemList( Class => $Class );
    die "general catalog class $Class is empty" if !$List || !%{$List};
    return { reverse %{$List} };
}

sub _Content { return [ undef, { Content => $_[0] } ] }

sub _Link {
    my (%P) = @_;
    my $Ok = $LinkObject->LinkAdd(
        SourceObject => $P{SourceObject},
        SourceKey    => $P{SourceKey},
        TargetObject => $P{TargetObject},
        TargetKey    => $P{TargetKey},
        Type         => $P{Type},
        State        => 'Valid',
        UserID       => 1,
    );
    die "LinkAdd $P{SourceObject}:$P{SourceKey} -> $P{TargetObject}:$P{TargetKey} failed" if !$Ok;
    return 1;
}

sub _NewCI {
    my (%P) = @_;
    my $ClassID = $P{ClassIDs}->{ $P{Class} } or die "unknown class $P{Class}";
    my $CIID = $CIObject->ConfigItemAdd( ClassID => $ClassID, UserID => 1 )
        or die "ConfigItemAdd $P{Class} failed";
    my $Definition = $CIObject->DefinitionGet( ClassID => $ClassID );
    for my $Version ( @{ $P{Versions} } ) {
        my $VersionID = $CIObject->VersionAdd(
            ConfigItemID => $CIID,
            Name         => $P{Name},
            DefinitionID => $Definition->{DefinitionID},
            DeplStateID  => $P{DeplStateIDs}->{ $Version->{Depl} },
            InciStateID  => $P{InciStateIDs}->{ $Version->{Inci} },
            XMLData      => [ undef, { Version => [ undef, $Version->{Data} ] } ],
            UserID       => 1,
        ) or die "VersionAdd $P{Name} failed";
    }
    my $CI = $CIObject->ConfigItemGet( ConfigItemID => $CIID );
    return { id => $CIID + 0, number => $CI->{Number}, name => $P{Name} };
}

my %Result;
if ($Existing) {
    # Report the rows of an earlier run in the same shape as a fresh one.
    my %CIs;
    for my $Pair ( [ web01 => 'web01.example.org' ], [ db01 => 'db01.example.org' ], [ net01 => 'net01.example.org' ] ) {
        my $IDs = $CIObject->ConfigItemSearchExtended( Name => $Pair->[1] ) || [];
        die "seeded CI $Pair->[1] not found" if @{$IDs} != 1;
        my $CI = $CIObject->ConfigItemGet( ConfigItemID => $IDs->[0] );
        $CIs{ $Pair->[0] } = { id => $IDs->[0] + 0, number => $CI->{Number}, name => $Pair->[1] };
    }
    my @Tickets = $TicketObject->TicketSearch(
        Title => 'itsm94 golden seed ticket', Result => 'ARRAY', UserID => 1, ArchiveFlags => [ 'y', 'n' ],
    );
    %Result = (
        created    => 0,
        service_id => $Existing + 0,
        sla_id     => $SLAObject->SLALookup( Name => 'itsm94-sla-web' ) + 0,
        ticket_id  => $Tickets[0] + 0,
        cis        => \%CIs,
    );
}
else {
    my $ClassIDs     = _Reverse('ITSM::ConfigItem::Class');
    my $DeplStateIDs = _Reverse('ITSM::ConfigItem::DeploymentState');
    my $InciStateIDs = _Reverse('ITSM::Core::IncidentState');
    my $ServiceTypes = _Reverse('ITSM::Service::Type');
    my $SLATypes     = _Reverse('ITSM::SLA::Type');

    my $ServiceID = $ServiceObject->ServiceAdd(
        Name        => $ServiceName,
        ValidID     => 1,
        Comment     => 'itsm94 golden seed service (web.example.org)',
        TypeID      => $ServiceTypes->{'End User Service'},
        Criticality => '4 high',
        UserID      => 1,
    ) or die 'ServiceAdd failed';

    my $SLAID = $SLAObject->SLAAdd(
        Name                    => 'itsm94-sla-web',
        ServiceIDs              => [$ServiceID],
        ValidID                 => 1,
        Comment                 => 'itsm94 golden seed SLA',
        TypeID                  => $SLATypes->{Availability},
        MinTimeBetweenIncidents => 120,
        UserID                  => 1,
    ) or die 'SLAAdd failed';

    my %Common = (
        ClassIDs     => $ClassIDs,
        DeplStateIDs => $DeplStateIDs,
        InciStateIDs => $InciStateIDs,
    );

    my $Web01 = _NewCI(
        %Common,
        Class    => 'Computer',
        Name     => 'web01.example.org',
        Versions => [
            {
                Depl => 'Planned',
                Inci => 'Operational',
                Data => {
                    Vendor      => _Content('Example Hardware'),
                    Description => _Content('itsm94 web server, first version'),
                    Owner       => _Content('itsm94-owner'),
                    FQDN        => _Content('web01.example.org'),
                    NIC         => [ undef, { Content => 'eth0', IPAddress => _Content('192.0.2.11') } ],
                },
            },
            {
                Depl => 'Production',
                Inci => 'Operational',
                Data => {
                    Vendor      => _Content('Example Hardware'),
                    Description => _Content('itsm94 web server, in production'),
                    Owner       => _Content('itsm94-owner'),
                    FQDN        => _Content('web01.example.org'),
                    NIC         => [ undef, { Content => 'eth0', IPAddress => _Content('192.0.2.11') } ],
                },
            },
        ],
    );
    my $Db01 = _NewCI(
        %Common,
        Class    => 'Computer',
        Name     => 'db01.example.org',
        Versions => [
            map {
                {
                    Depl => $_->[0],
                    Inci => 'Operational',
                    Data => {
                        Vendor      => _Content('Example Hardware'),
                        Description => _Content( $_->[1] ),
                        FQDN        => _Content('db01.example.org'),
                        NIC         => [ undef, { Content => 'eth0', IPAddress => _Content('192.0.2.12') } ],
                    },
                }
            } ( [ 'Planned', 'itsm94 database, planned' ], [ 'Test/QA', 'itsm94 database, test' ], [ 'Production', 'itsm94 database, in production' ] )
        ],
    );
    my $Net01 = _NewCI(
        %Common,
        Class    => 'Network',
        Name     => 'net01.example.org',
        Versions => [
            {
                Depl => 'Production',
                Inci => 'Incident',
                Data => {
                    Description    => _Content('itsm94 office network, link down'),
                    NetworkAddress => [ undef, { Content => '192.0.2.0/24', Gateway => _Content('192.0.2.1') } ],
                },
            },
        ],
    );

    # CI -> service (DependsOn): the service depends on the CI
    _Link( SourceObject => 'Service', SourceKey => $ServiceID, TargetObject => 'ITSMConfigItem', TargetKey => $Web01->{id}, Type => 'DependsOn' );
    _Link( SourceObject => 'Service', SourceKey => $ServiceID, TargetObject => 'ITSMConfigItem', TargetKey => $Net01->{id}, Type => 'DependsOn' );
    # CI <-> CI
    _Link( SourceObject => 'ITSMConfigItem', SourceKey => $Web01->{id}, TargetObject => 'ITSMConfigItem', TargetKey => $Db01->{id}, Type => 'DependsOn' );
    _Link( SourceObject => 'ITSMConfigItem', SourceKey => $Web01->{id}, TargetObject => 'ITSMConfigItem', TargetKey => $Net01->{id}, Type => 'DependsOn' );

    # ticket <-> CI
    my $TicketID = $TicketObject->TicketCreate(
        Title        => 'itsm94 golden seed ticket',
        Queue        => 'Raw',
        Lock         => 'unlock',
        Priority     => '3 normal',
        State        => 'new',
        CustomerID   => 'golden-customer',
        CustomerUser => 'golden.customer@example.org',
        OwnerID      => 1,
        UserID       => 1,
    ) or die 'TicketCreate failed';
    _Link( SourceObject => 'Ticket', SourceKey => $TicketID, TargetObject => 'ITSMConfigItem', TargetKey => $Web01->{id}, Type => 'RelevantTo' );

    %Result = (
        created    => 1,
        service_id => $ServiceID + 0,
        sla_id     => $SLAID + 0,
        ticket_id  => $TicketID + 0,
        cis        => { web01 => $Web01, db01 => $Db01, net01 => $Net01 },
    );
}

print "GOLDEN_ITSM_SEED_BEGIN\n" . $JSON->Encode( Data => \%Result ) . "\nGOLDEN_ITSM_SEED_END\n";
