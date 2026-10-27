#!/usr/bin/perl
# Seed ITSM fixtures into a golden ITSM peer (I15). Copy in with `znuny_copy_in`, run with
# `perl /tmp/itsm_seed.pl` as user otrs (see test_itsm_peer_smoke.py).
#
#   * service itsm94-svc-web (criticality "4 high", type "End User Service") + SLA
#     itsm94-sla-web with MinTimeBetweenIncidents
#   * CIs in the default classes: Computer x2 (2 and 3 versions), Network x1 (incident state)
#   * links CI->service (DependsOn), CI<->CI (DependsOn), ticket<->CI (RelevantTo)
#   * change management (ITSMChangeManagement, wave-5 itsm-change Task 1): three changes
#     (chg94 ...) with 2-3 work orders each (types workorder/backout/pir, one work order without
#     times), a CAB with one agent and one customer user, change<->ticket, work order<->CI/service/
#     ticket links, one change and one work order dynamic field, one change template. Skipped when
#     the ITSMChangeManagement package is absent. The history comes from the package's event
#     modules (never SQL).
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

sub _SeedChanges {
    my ($Base) = @_;
    my $ChangeObject = $Kernel::OM->Get('Kernel::System::ITSMChange');
    my $WOObject     = $Kernel::OM->Get('Kernel::System::ITSMChange::ITSMWorkOrder');
    my $TemplateObject = $Kernel::OM->Get('Kernel::System::ITSMChange::Template');
    my $DFObject     = $Kernel::OM->Get('Kernel::System::DynamicField');
    my $UserObject   = $Kernel::OM->Get('Kernel::System::User');
    my $GroupObject  = $Kernel::OM->Get('Kernel::System::Group');
    my $CustomerObject = $Kernel::OM->Get('Kernel::System::CustomerUser');

    # Znuny rejects @example.org as "invalid" (CheckEmailInvalidAddress); the fixtures use it on purpose.
    $Kernel::OM->Get('Kernel::Config')->Set( Key => 'CheckEmailAddresses', Value => 0 );

    # --- people: one agent (CAB, change manager, work order agent), one customer user -------------
    my $AgentLogin = 'chg94.agent';
    my $AgentID    = $UserObject->UserLookup( UserLogin => $AgentLogin, Silent => 1 );
    if ( !$AgentID ) {
        $AgentID = $UserObject->UserAdd(
            UserFirstname => 'Erika',
            UserLastname  => 'Beispiel',
            UserLogin     => $AgentLogin,
            UserEmail     => 'chg94.agent@example.org',
            UserPw        => 'chg94-not-a-password',
            ValidID       => 1,
            ChangeUserID  => 1,
        ) or die 'UserAdd chg94.agent failed';
    }
    # a second agent in no group at all (the permission probe needs one that must be refused)
    my $NoGroupLogin = 'chg94.nogroup';
    if ( !$UserObject->UserLookup( UserLogin => $NoGroupLogin, Silent => 1 ) ) {
        $UserObject->UserAdd(
            UserFirstname => 'Hans',
            UserLastname  => 'Beispiel',
            UserLogin     => $NoGroupLogin,
            UserEmail     => 'chg94.nogroup@example.org',
            UserPw        => 'chg94-not-a-password',
            ValidID       => 1,
            ChangeUserID  => 1,
        ) or die 'UserAdd chg94.nogroup failed';
    }
    for my $Group (qw(itsm-change itsm-change-builder itsm-change-manager)) {
        my $GroupID = $GroupObject->GroupLookup( Group => $Group ) or die "group $Group missing";
        $GroupObject->PermissionGroupUserAdd(
            GID => $GroupID, UID => $AgentID, Permission => { ro => 1, rw => 1 }, UserID => 1,
        ) or die "PermissionGroupUserAdd $Group failed";
    }
    my $CustomerLogin = 'chg94-cust@example.org';
    my %Customer      = $CustomerObject->CustomerUserDataGet( User => $CustomerLogin );
    if ( !%Customer ) {
        $CustomerObject->CustomerUserAdd(
            Source         => 'CustomerUser',
            UserFirstname  => 'Max',
            UserLastname   => 'Mustermann',
            UserCustomerID => 'chg94-customer',
            UserLogin      => $CustomerLogin,
            UserEmail      => $CustomerLogin,
            ValidID        => 1,
            UserID         => 1,
        ) or die 'CustomerUserAdd failed';
    }

    # --- dynamic fields of the two object types -------------------------------------------------
    my %DF;
    for my $Def ( [ chg94ChangeRef => 'ITSMChange' ], [ chg94WorkOrderRef => 'ITSMWorkOrder' ] ) {
        # DynamicFieldGet answers an empty (but true) hash for an unknown name
        my $Existing = $DFObject->DynamicFieldGet( Name => $Def->[0] );
        $DF{ $Def->[0] } = ( $Existing && $Existing->{ID} ) ? $Existing->{ID} : $DFObject->DynamicFieldAdd(
            Name       => $Def->[0],
            Label      => $Def->[0],
            FieldOrder => 9000,
            FieldType  => 'Text',
            ObjectType => $Def->[1],
            Config     => { DefaultValue => '' },
            ValidID    => 1,
            UserID     => 1,
        ) or die "DynamicFieldAdd $Def->[0] failed";
    }

    my $Out = { agent_id => $AgentID + 0, customer_login => $CustomerLogin, dynamic_fields => \%DF, changes => {} };

    my @Specs = (
        {
            key     => 'mail',
            title   => 'chg94 Mail server replacement',
            state   => 'in progress',
            cab     => 1,
            ref     => 'CHG-94-MAIL',
            update  => {
                Description   => '<p>Replace the <b>mail</b> server mail01.example.org</p>',
                Justification => '<p>Old hardware out of support</p>',
                Category      => '3 normal',
                Impact        => '4 high',
                RequestedTime => '2026-11-01 00:00:00',
            },
            workorders => [
                {
                    title => 'chg94 install mail02', type => 'workorder', state => 'closed',
                    agent => $AgentID, ref => 'WO-94-1',
                    instruction => '<p>Install <i>mail02.example.org</i></p>', report => '<p>done</p>',
                    planned => [ '2026-11-02 08:00:00', '2026-11-02 12:00:00' ],
                    actual  => [ '2026-11-02 08:05:00', '2026-11-02 12:30:00' ],
                    effort => 4, accounted => 4.5,
                    links => [ [ ITSMConfigItem => $Base->{cis}{web01}{id}, 'DependsOn' ], [ Service => $Base->{service_id}, 'Normal' ] ],
                },
                {
                    title => 'chg94 backout mail', type => 'backout', state => 'created', agent => $AgentID,
                    planned => [ '2026-11-02 12:00:00', '2026-11-02 14:00:00' ],
                    links => [ [ Ticket => $Base->{ticket_id}, 'Normal' ] ],
                },
                { title => 'chg94 review mail', type => 'pir', state => 'created' },
            ],
        },
        {
            key     => 'firmware',
            title   => 'chg94 Switch firmware rollout',
            state   => 'requested',
            cab     => 0,
            ref     => '',
            update  => { Description => 'Plain text description', Justification => 'Security fix', Category => '2 low', Impact => '2 low' },
            workorders => [
                {
                    title => 'chg94 flash switches', type => 'workorder', state => 'ready',
                    planned => [ '2026-11-09 18:00:00', '2026-11-09 20:00:00' ],
                },
                {
                    title => 'chg94 backout firmware', type => 'backout', state => 'created',
                    planned => [ '2026-11-09 20:00:00', '2026-11-09 21:00:00' ],
                },
            ],
        },
        {
            key     => 'database',
            title   => 'chg94 Database migration',
            state   => 'successful',
            cab     => 0,
            ref     => '',
            update  => { Description => '<p>Move db01.example.org to new storage</p>', Justification => '<p>Capacity</p>', Category => '4 high', Impact => '3 normal' },
            workorders => [
                {
                    title => 'chg94 migrate db', type => 'workorder', state => 'closed',
                    planned => [ '2026-10-05 08:00:00', '2026-10-05 10:00:00' ],
                    actual  => [ '2026-10-05 08:05:00', '2026-10-05 10:30:00' ],
                    links => [ [ ITSMConfigItem => $Base->{cis}{db01}{id}, 'DependsOn' ] ],
                },
                {
                    title => 'chg94 review db', type => 'pir', state => 'closed',
                    planned => [ '2026-10-06 09:00:00', '2026-10-06 10:00:00' ],
                    actual  => [ '2026-10-06 09:00:00', '2026-10-06 09:45:00' ],
                },
            ],
        },
    );

    for my $Spec (@Specs) {
        my $Found = $ChangeObject->ChangeSearch( ChangeTitle => $Spec->{title}, UserID => 1 ) || [];
        my $ChangeID = $Found->[0];
        if ( !$ChangeID ) {
            $ChangeID = $ChangeObject->ChangeAdd( ChangeTitle => $Spec->{title}, ChangeBuilderID => 1, UserID => 1 )
                or die "ChangeAdd $Spec->{title} failed";
            $ChangeObject->ChangeUpdate(
                ChangeID => $ChangeID,
                %{ $Spec->{update} },
                ChangeManagerID => $AgentID,
                ( $Spec->{ref} ? ( DynamicField_chg94ChangeRef => $Spec->{ref} ) : () ),
                ( $Spec->{cab} ? ( CABAgents => [$AgentID], CABCustomers => [$CustomerLogin] ) : (CABAgents => [1]) ),
                UserID => 1,
            ) or die "ChangeUpdate $Spec->{title} failed";
            my %WOIDs;
            for my $WO ( @{ $Spec->{workorders} } ) {
                my $WOID = $WOObject->WorkOrderAdd(
                    ChangeID => $ChangeID, WorkOrderTitle => $WO->{title}, WorkOrderType => $WO->{type},
                    ( $WO->{instruction} ? ( Instruction => $WO->{instruction} ) : () ),
                    ( $WO->{agent} ? ( WorkOrderAgentID => $WO->{agent} ) : () ),
                    ( $WO->{planned} ? ( PlannedStartTime => $WO->{planned}[0], PlannedEndTime => $WO->{planned}[1] ) : () ),
                    ( $WO->{effort} ? ( PlannedEffort => $WO->{effort} ) : () ),
                    ( $WO->{ref} ? ( DynamicField_chg94WorkOrderRef => $WO->{ref} ) : () ),
                    UserID => 1,
                ) or die "WorkOrderAdd $WO->{title} failed";
                if ( $WO->{state} ne 'created' ) {
                    $WOObject->WorkOrderUpdate(
                        WorkOrderID => $WOID, WorkOrderState => $WO->{state}, BypassStateMachine => 1,
                        ( $WO->{report} ? ( Report => $WO->{report} ) : () ),
                        ( $WO->{accounted} ? ( AccountedTime => $WO->{accounted} ) : () ),
                        ( $WO->{actual} ? ( ActualStartTime => $WO->{actual}[0], ActualEndTime => $WO->{actual}[1] ) : () ),
                        UserID => 1,
                    ) or die "WorkOrderUpdate $WO->{title} failed";
                }
                for my $L ( @{ $WO->{links} || [] } ) {
                    _Link( SourceObject => 'ITSMWorkOrder', SourceKey => $WOID, TargetObject => $L->[0], TargetKey => $L->[1], Type => $L->[2] );
                }
                $WOIDs{ $WO->{title} } = $WOID + 0;
            }
            $ChangeObject->ChangeUpdate(
                ChangeID => $ChangeID, ChangeState => $Spec->{state}, BypassStateMachine => 1, UserID => 1,
            ) or die "ChangeUpdate state $Spec->{title} failed";
        }
        if ( $Spec->{key} eq 'mail' ) {
            my $Linked = $Kernel::OM->Get('Kernel::System::LinkObject')->LinkList(
                Object => 'ITSMChange', Key => $ChangeID, State => 'Valid', UserID => 1,
            ) || {};
            _Link( SourceObject => 'ITSMChange', SourceKey => $ChangeID, TargetObject => 'Ticket', TargetKey => $Base->{ticket_id}, Type => 'Normal' )
                if !$Linked->{Ticket};
        }
        $Out->{changes}{ $Spec->{key} } = { id => $ChangeID + 0 };
    }

    # --- one change template (content is a Data::Dumper string, not parsed by Tiqora) -------------
    my $TemplateName = 'chg94 template firmware';
    my $Templates = $TemplateObject->TemplateSearch( TemplateName => $TemplateName, UserID => 1 ) || [];
    if ( !@{$Templates} ) {
        my $Content = $TemplateObject->TemplateSerialize(
            TemplateType => 'ITSMChange', ChangeID => $Out->{changes}{firmware}{id}, StateReset => 1, UserID => 1,
        ) or die 'TemplateSerialize failed';
        $TemplateObject->TemplateAdd(
            Name => $TemplateName, Comment => 'chg94 template', TemplateType => 'ITSMChange',
            Content => $Content, ValidID => 1, UserID => 1,
        ) or die 'TemplateAdd failed';
    }
    return $Out;
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

%Result = ( %Result, changes => _SeedChanges( \%Result ) );

print "GOLDEN_ITSM_SEED_BEGIN\n" . $JSON->Encode( Data => \%Result ) . "\nGOLDEN_ITSM_SEED_END\n";
