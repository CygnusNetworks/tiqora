#!/usr/bin/perl
# Behaviour scenarios of the Znuny ITSM packages (wave-4 itsm-core, Task 3).
#
# Copied into a golden ITSM peer by `tests/golden/itsm_survey.py <peer> --behaviour` and run as
# the Znuny user. Creates its own data (names itsm-bhv-*.example.org) and prints one JSON object
# between GOLDEN_ITSM_BEHAVIOUR_BEGIN / GOLDEN_ITSM_BEHAVIOUR_END. Needs no seed run and may be
# run once per peer (a second run reports `already_run`).
use strict;
use warnings;
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;

local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => { LogPrefix => 'golden-itsm-behaviour' },
);

my $ServiceObject = $Kernel::OM->Get('Kernel::System::Service');
my $CIObject      = $Kernel::OM->Get('Kernel::System::ITSMConfigItem');
my $LinkObject    = $Kernel::OM->Get('Kernel::System::LinkObject');
my $GCObject      = $Kernel::OM->Get('Kernel::System::GeneralCatalog');
my $TicketObject  = $Kernel::OM->Get('Kernel::System::Ticket');
my $CIPObject     = $Kernel::OM->Get('Kernel::System::ITSMCIPAllocate');
my $DBObject      = $Kernel::OM->Get('Kernel::System::DB');
my $GroupObject   = $Kernel::OM->Get('Kernel::System::Group');
my $UserObject    = $Kernel::OM->Get('Kernel::System::User');
my $JSON          = $Kernel::OM->Get('Kernel::System::JSON');
my $ConfigObject  = $Kernel::OM->Get('Kernel::Config');

my $Prefix = 'itsm-bhv';

sub _Reverse {
    my ($Class) = @_;
    my $List = $GCObject->ItemList( Class => $Class );
    die "general catalog class $Class is empty" if !$List || !%{$List};
    return { reverse %{$List} };
}

my $ClassIDs     = _Reverse('ITSM::ConfigItem::Class');
my $DeplStateIDs = _Reverse('ITSM::ConfigItem::DeploymentState');
my $InciStateIDs = _Reverse('ITSM::Core::IncidentState');
my $ServiceTypes = _Reverse('ITSM::Service::Type');

if ( $ServiceObject->ServiceLookup( Name => "$Prefix-s1" ) ) {
    print "GOLDEN_ITSM_BEHAVIOUR_BEGIN\n", $JSON->Encode( Data => { already_run => 1 } ), "\nGOLDEN_ITSM_BEHAVIOUR_END\n";
    exit 0;
}

sub _Rows {
    my (%P) = @_;
    $DBObject->Prepare( SQL => $P{SQL}, Bind => $P{Bind} || [] ) or die "SQL failed: $P{SQL}";
    my @Rows;
    while ( my @Row = $DBObject->FetchrowArray() ) { push @Rows, [ map { defined $_ ? "$_" : undef } @Row ]; }
    return \@Rows;
}

sub _NewCI {
    my (%P) = @_;
    my $ClassID = $ClassIDs->{Computer};
    my $CIID    = $CIObject->ConfigItemAdd( ClassID => $ClassID, UserID => 1 ) or die 'ConfigItemAdd failed';
    my $Definition = $CIObject->DefinitionGet( ClassID => $ClassID );
    $CIObject->VersionAdd(
        ConfigItemID => $CIID,
        Name         => $P{Name},
        DefinitionID => $Definition->{DefinitionID},
        DeplStateID  => $DeplStateIDs->{ $P{Depl} },
        InciStateID  => $InciStateIDs->{ $P{Inci} },
        XMLData      => [ undef, { Version => [ undef, { FQDN => [ undef, { Content => $P{Name} } ] } ] } ],
        UserID       => 1,
    ) or die "VersionAdd $P{Name} failed";
    return $CIID + 0;
}

sub _Service {
    my ( $Name, $ParentID ) = @_;
    return $ServiceObject->ServiceAdd( Name => $Name, ParentID => $ParentID, ValidID => 1, TypeID => $ServiceTypes->{'End User Service'}, Criticality => '3 normal', UserID => 1 )
        or die "ServiceAdd $Name failed";
}

sub _Link {
    my (%P) = @_;
    return $LinkObject->LinkAdd(
        SourceObject => $P{SO}, SourceKey => $P{SK}, TargetObject => $P{TO}, TargetKey => $P{TK},
        Type => $P{Type}, State => $P{State} || 'Valid', UserID => 1,
    ) ? 1 : 0;
}

sub _Unlink {
    my (%P) = @_;
    return $LinkObject->LinkDelete(
        Object1 => $P{O1}, Key1 => $P{K1}, Object2 => $P{O2}, Key2 => $P{K2}, Type => $P{Type}, UserID => 1,
    ) ? 1 : 0;
}

sub _CI {
    my ($ID) = @_;
    my $CI = $CIObject->ConfigItemGet( ConfigItemID => $ID, Cache => 0 );
    return lc( $CI->{CurInciStateType} // '' ) . '/' . lc( $CI->{CurDeplStateType} // '' );
}

sub _CIInci {
    my ($ID) = @_;
    return $CIObject->ConfigItemGet( ConfigItemID => $ID, Cache => 0 )->{CurInciStateType};
}

sub _Svc {
    my ($ID) = @_;
    my %S = $ServiceObject->ServiceGet( ServiceID => $ID, IncidentState => 1, UserID => 1 );
    return { type => $S{CurInciStateType}, state => $S{CurInciState} };
}

sub _SvcPref {
    my ($ID) = @_;
    my %P = $ServiceObject->ServicePreferencesGet( ServiceID => $ID );
    return $P{CurInciStateTypeFromCIs};
}

my %Out;

# --- 1. CIP allocation: the 25 cells and the edge cases --------------------------------------
{
    my $DFObject  = $Kernel::OM->Get('Kernel::System::DynamicField');
    my $DFBackend = $Kernel::OM->Get('Kernel::System::DynamicField::Backend');
    my %Possible;
    for my $Name (qw(ITSMCriticality ITSMImpact)) {
        my $DF = $DFObject->DynamicFieldGet( Name => $Name ) or die "dynamic field $Name missing";
        $Possible{$Name} = $DFBackend->PossibleValuesGet( DynamicFieldConfig => $DF );
    }
    # the dropdowns offer an empty choice ('' => '-'); it is not a cell
    my $Criticality = { map { $_ => $Possible{ITSMCriticality}{$_} } grep { length } keys %{ $Possible{ITSMCriticality} } };
    my $Impact      = { map { $_ => $Possible{ITSMImpact}{$_} } grep { length } keys %{ $Possible{ITSMImpact} } };
    my @Cells;
    for my $I ( sort keys %{$Impact} ) {
        for my $C ( sort keys %{$Criticality} ) {
            push @Cells, { criticality => $C, impact => $I, priority_id => $CIPObject->PriorityAllocationGet( Criticality => $C, Impact => $I ) + 0 };
        }
    }
    my $EmptyImpact = $CIPObject->PriorityAllocationGet( Criticality => '3 normal', Impact => '' );
    my $Missing = $CIPObject->PriorityAllocationGet( Criticality => '9 unknown', Impact => '3 normal' );
    my $NoImpact = $CIPObject->PriorityAllocationGet( Criticality => '3 normal' );
    my $Upper = $CIPObject->PriorityAllocationGet( Criticality => '3 NORMAL', Impact => '3 normal' );
    $Out{cip} = {
        cells               => \@Cells,
        unknown_value       => defined $Missing  ? $Missing + 0  : undef,
        empty_impact        => defined $EmptyImpact ? $EmptyImpact + 0 : undef,
        missing_argument    => defined $NoImpact ? $NoImpact + 0 : undef,
        other_case_returns  => defined $Upper    ? $Upper + 0    : undef,
        criticality_values  => $Criticality,
        impact_values       => $Impact,
    };
}

# --- 2. service incident state (default IncidentLinkTypeDirection DependsOn => Both) ----------
$Out{incident_link_type_direction} = $ConfigObject->Get('ITSM::Core::IncidentLinkTypeDirection');
my %Svc;
{
    # s1: service -> DependsOn -> productive CI in incident
    my $S1 = _Service("$Prefix-s1");
    my $A  = _NewCI( Name => "$Prefix-a.example.org", Depl => 'Production', Inci => 'Incident' );
    _Link( SO => 'Service', SK => $S1, TO => 'ITSMConfigItem', TK => $A, Type => 'DependsOn' );
    $Svc{s1_incident_ci} = { service => _Svc($S1), preference => _SvcPref($S1), ci => _CIInci($A) };

    # s2: warning travels along DependsOn (Both) from the incident CI to its neighbour
    my $S2 = _Service("$Prefix-s2");
    my $W  = _NewCI( Name => "$Prefix-w.example.org", Depl => 'Production', Inci => 'Operational' );
    my $I2 = _NewCI( Name => "$Prefix-i2.example.org", Depl => 'Production', Inci => 'Incident' );
    _Link( SO => 'Service', SK => $S2, TO => 'ITSMConfigItem', TK => $W, Type => 'DependsOn' );
    _Link( SO => 'ITSMConfigItem', SK => $W, TO => 'ITSMConfigItem', TK => $I2, Type => 'DependsOn' );
    $Svc{s2_warning_via_dependency} = { service => _Svc($S2), ci_neighbour => _CIInci($W), ci_incident => _CIInci($I2) };

    # s2b: ... and in the opposite direction (the incident CI depends on the neighbour)
    my $S2b = _Service("$Prefix-s2b");
    my $Wb  = _NewCI( Name => "$Prefix-wb.example.org", Depl => 'Production', Inci => 'Operational' );
    my $Ib  = _NewCI( Name => "$Prefix-ib.example.org", Depl => 'Production', Inci => 'Incident' );
    _Link( SO => 'Service', SK => $S2b, TO => 'ITSMConfigItem', TK => $Wb, Type => 'DependsOn' );
    _Link( SO => 'ITSMConfigItem', SK => $Ib, TO => 'ITSMConfigItem', TK => $Wb, Type => 'DependsOn' );
    $Svc{s2b_warning_opposite_direction} = { service => _Svc($S2b), ci_neighbour => _CIInci($Wb) };

    # s2c: transitive, two hops
    my $Hop1 = _NewCI( Name => "$Prefix-h1.example.org", Depl => 'Production', Inci => 'Operational' );
    my $Hop2 = _NewCI( Name => "$Prefix-h2.example.org", Depl => 'Production', Inci => 'Operational' );
    my $Hi   = _NewCI( Name => "$Prefix-hi.example.org", Depl => 'Production', Inci => 'Incident' );
    _Link( SO => 'ITSMConfigItem', SK => $Hop1, TO => 'ITSMConfigItem', TK => $Hop2, Type => 'DependsOn' );
    _Link( SO => 'ITSMConfigItem', SK => $Hop2, TO => 'ITSMConfigItem', TK => $Hi,   Type => 'DependsOn' );
    $Svc{s2c_transitive_two_hops} = { hop1 => _CIInci($Hop1), hop2 => _CIInci($Hop2) };

    # s3: incident CI that is not productive does not count
    my $S3 = _Service("$Prefix-s3");
    my $P  = _NewCI( Name => "$Prefix-p.example.org", Depl => 'Planned', Inci => 'Incident' );
    _Link( SO => 'Service', SK => $S3, TO => 'ITSMConfigItem', TK => $P, Type => 'DependsOn' );
    $Svc{s3_non_productive_ignored} = { service => _Svc($S3), ci => _CIInci($P), ci_deployment_type => $CIObject->ConfigItemGet( ConfigItemID => $P, Cache => 0 )->{CurDeplStateType} };

    # s4: ranking, incident beats warning
    my $S4 = _Service("$Prefix-s4");
    my $W4 = _NewCI( Name => "$Prefix-w4.example.org", Depl => 'Production', Inci => 'Operational' );
    my $I4 = _NewCI( Name => "$Prefix-i4.example.org", Depl => 'Production', Inci => 'Incident' );
    my $X4 = _NewCI( Name => "$Prefix-x4.example.org", Depl => 'Production', Inci => 'Incident' );
    _Link( SO => 'ITSMConfigItem', SK => $W4, TO => 'ITSMConfigItem', TK => $I4, Type => 'DependsOn' );
    _Link( SO => 'Service', SK => $S4, TO => 'ITSMConfigItem', TK => $W4, Type => 'DependsOn' );
    my $Before = _Svc($S4);
    _Link( SO => 'Service', SK => $S4, TO => 'ITSMConfigItem', TK => $X4, Type => 'DependsOn' );
    $Svc{s4_incident_beats_warning} = { service_only_warning_ci => $Before, service_with_incident_ci => _Svc($S4) };

    # s5: a link type outside IncidentLinkTypeDirection does not count
    my $S5 = _Service("$Prefix-s5");
    my $R  = _NewCI( Name => "$Prefix-r.example.org", Depl => 'Production', Inci => 'Incident' );
    _Link( SO => 'Service', SK => $S5, TO => 'ITSMConfigItem', TK => $R, Type => 'RelevantTo' );
    $Svc{s5_relevant_to_not_counted} = { service => _Svc($S5), ci => _CIInci($R) };

    # s6: child service, parent turns warning only through its direct children
    my $Parent = _Service("$Prefix-par");
    my $Child  = _Service( 'child', $Parent );
    my $C6     = _NewCI( Name => "$Prefix-c6.example.org", Depl => 'Production', Inci => 'Incident' );
    _Link( SO => 'Service', SK => $Child, TO => 'ITSMConfigItem', TK => $C6, Type => 'DependsOn' );
    $Svc{s6_child_service_makes_parent_warning} = { parent => _Svc($Parent), child => _Svc($Child) };

    # s7: unlinking recalculates (s2: remove the dependency, neighbour and service recover)
    _Unlink( O1 => 'ITSMConfigItem', K1 => $W, O2 => 'ITSMConfigItem', K2 => $I2, Type => 'DependsOn' );
    $Svc{s7_unlink_recovers} = { service => _Svc($S2), ci_neighbour => _CIInci($W) };

    # s8: a new version of the incident CI with state Operational recovers service s1
    my $Def = $CIObject->DefinitionGet( ClassID => $ClassIDs->{Computer} );
    $CIObject->VersionAdd(
        ConfigItemID => $A, Name => "$Prefix-a.example.org", DefinitionID => $Def->{DefinitionID},
        DeplStateID  => $DeplStateIDs->{Production}, InciStateID => $InciStateIDs->{Operational},
        XMLData      => [ undef, { Version => [ undef, { FQDN => [ undef, { Content => "$Prefix-a.example.org" } ] } ] } ],
        UserID       => 1,
    ) or die 'VersionAdd (recovery) failed';
    $Svc{s8_new_operational_version_recovers} = { service => _Svc($S1), preference => _SvcPref($S1), ci => _CIInci($A) };
}
$Out{service_incident} = \%Svc;

# --- 5/9. link side effects --------------------------------------------------------------------
{
    my $CI = _NewCI( Name => "$Prefix-link.example.org", Depl => 'Production', Inci => 'Operational' );
    my $T1 = $TicketObject->TicketCreate(
        Title => "$Prefix ticket one", Queue => 'Raw', Lock => 'unlock', Priority => '3 normal', State => 'new',
        OwnerID => 1, UserID => 1,
    ) or die 'TicketCreate 1 failed';
    my $T2 = $TicketObject->TicketCreate(
        Title => "$Prefix ticket two", Queue => 'Raw', Lock => 'unlock', Priority => '3 normal', State => 'new',
        OwnerID => 1, UserID => 1,
    ) or die 'TicketCreate 2 failed';
    my $N1 = $TicketObject->TicketNumberLookup( TicketID => $T1 );
    my $N2 = $TicketObject->TicketNumberLookup( TicketID => $T2 );

    my $TicketHist = sub {
        my ($TicketID) = @_;
        return _Rows(
            SQL  => "SELECT ht.name, th.name FROM ticket_history th JOIN ticket_history_type ht ON ht.id = th.history_type_id WHERE th.ticket_id = ? AND ht.name IN ('CILinkAdd','CILinkDelete','TicketLinkAdd','TicketLinkDelete') ORDER BY th.id",
            Bind => [ \$TicketID ],
        );
    };
    my $CIHist = sub {
        return _Rows(
            SQL  => "SELECT ht.name, h.content FROM configitem_history h JOIN configitem_history_type ht ON ht.id = h.type_id WHERE h.configitem_id = ? AND ht.name IN ('LinkAdd','LinkDelete') ORDER BY h.id",
            Bind => [ \$CI ],
        );
    };
    my $Relations = sub {
        return _Rows( SQL => 'SELECT count(*) FROM link_relation WHERE (source_key = ? AND source_object_id = (SELECT id FROM link_object WHERE name = ?)) OR (target_key = ? AND target_object_id = (SELECT id FROM link_object WHERE name = ?))', Bind => [ \$CI, \'ITSMConfigItem', \$CI, \'ITSMConfigItem' ] )->[0][0] + 0;
    };

    my %R;
    # ticket (source) -> CI (target), RelevantTo, Valid
    $R{ticket_to_ci_add} = { ok => _Link( SO => 'Ticket', SK => $T1, TO => 'ITSMConfigItem', TK => $CI, Type => 'RelevantTo' ) };
    $R{ticket_to_ci_add}{ticket_history} = $TicketHist->($T1);
    $R{ticket_to_ci_add}{ci_history}     = $CIHist->();
    $R{ticket_to_ci_add}{link_row}       = _Rows( SQL => 'SELECT o1.name, r.source_key, o2.name, r.target_key, t.name, s.name FROM link_relation r JOIN link_object o1 ON o1.id = r.source_object_id JOIN link_object o2 ON o2.id = r.target_object_id JOIN link_type t ON t.id = r.type_id JOIN link_state s ON s.id = r.state_id WHERE r.source_object_id = (SELECT id FROM link_object WHERE name = ?) AND r.source_key = ? AND r.target_key = ?', Bind => [ \'Ticket', \$T1, \$CI ] );
    # the same pair again (the link already exists)
    $R{ticket_to_ci_add_again} = { ok => _Link( SO => 'Ticket', SK => $T1, TO => 'ITSMConfigItem', TK => $CI, Type => 'RelevantTo' ), relations => $Relations->() };
    # type that is not configured for ticket <-> CI
    $R{ticket_to_ci_not_possible_type} = { ok => _Link( SO => 'Ticket', SK => $T1, TO => 'ITSMConfigItem', TK => $CI, Type => 'ConnectedTo' ) };
    $R{ticket_to_ci_normal_type} = { ok => _Link( SO => 'Ticket', SK => $T1, TO => 'ITSMConfigItem', TK => $CI, Type => 'Normal' ) };
    # delete (Valid)
    $R{ticket_to_ci_delete} = { ok => _Unlink( O1 => 'Ticket', K1 => $T1, O2 => 'ITSMConfigItem', K2 => $CI, Type => 'RelevantTo' ) };
    $R{ticket_to_ci_delete}{ticket_history} = $TicketHist->($T1);
    $R{ticket_to_ci_delete}{ci_history}     = $CIHist->();
    $R{ticket_to_ci_delete}{relations}      = $Relations->();
    # delete of a link that does not exist
    $R{delete_missing_link_returns} = _Unlink( O1 => 'Ticket', K1 => $T1, O2 => 'ITSMConfigItem', K2 => $CI, Type => 'RelevantTo' );
    # Temporary link: no history, still deleted by LinkDelete without state
    my $HistBefore = scalar @{ $TicketHist->($T2) };
    $R{temporary_add} = { ok => _Link( SO => 'Ticket', SK => $T2, TO => 'ITSMConfigItem', TK => $CI, Type => 'RelevantTo', State => 'Temporary' ) };
    $R{temporary_add}{ticket_history_rows_added} = scalar( @{ $TicketHist->($T2) } ) - $HistBefore;
    $R{temporary_delete} = { ok => _Unlink( O1 => 'Ticket', K1 => $T2, O2 => 'ITSMConfigItem', K2 => $CI, Type => 'RelevantTo' ) };
    $R{temporary_delete}{ticket_history_rows_added} = scalar( @{ $TicketHist->($T2) } ) - $HistBefore;
    $R{temporary_delete}{relations} = $Relations->();
    # ticket <-> ticket (core): history type and name format
    $R{ticket_ticket_add}    = { ok => _Link( SO => 'Ticket', SK => $T1, TO => 'Ticket', TK => $T2, Type => 'ParentChild' ) };
    $R{ticket_ticket_add}{t1_history} = $TicketHist->($T1);
    $R{ticket_ticket_add}{t2_history} = $TicketHist->($T2);
    $R{ticket_ticket_delete} = { ok => _Unlink( O1 => 'Ticket', K1 => $T2, O2 => 'Ticket', K2 => $T1, Type => 'ParentChild' ) };
    $R{ticket_ticket_delete}{t1_history} = $TicketHist->($T1);
    $R{ticket_ticket_delete}{t2_history} = $TicketHist->($T2);
    # same-type-group conflict (Normal and ParentChild share a type group)
    _Link( SO => 'Ticket', SK => $T1, TO => 'Ticket', TK => $T2, Type => 'Normal' );
    $R{ticket_ticket_type_group_conflict} = { ok => _Link( SO => 'Ticket', SK => $T1, TO => 'Ticket', TK => $T2, Type => 'ParentChild' ) };
    $R{ticket_numbers} = { t1 => $N1, t2 => $N2 };
    $Out{link_side_effects} = \%R;
}


# --- 6a. link types: pointed flag and the object pairs they are offered for --------------------
{
    my %Types = $LinkObject->TypeList( UserID => 1 );
    my %T;
    for my $Name ( sort keys %Types ) {
        my $TypeID = $LinkObject->TypeLookup( Name => $Name, UserID => 1 );
        my %Data   = $LinkObject->TypeGet( TypeID => $TypeID, UserID => 1 );
        $T{$Name} = { source_name => $Data{SourceName}, target_name => $Data{TargetName}, pointed => $Data{Pointed} + 0 };
    }
    my %Pairs;
    for my $Pair ( [ 'Ticket', 'Ticket' ], [ 'Ticket', 'ITSMConfigItem' ], [ 'ITSMConfigItem', 'ITSMConfigItem' ], [ 'ITSMConfigItem', 'Service' ], [ 'Service', 'Ticket' ] ) {
        my %Possible = $LinkObject->PossibleTypesList( Object1 => $Pair->[0], Object2 => $Pair->[1] );
        $Pairs{ $Pair->[0] . '/' . $Pair->[1] } = [ sort keys %Possible ];
    }
    # the order of the objects does not matter
    my %Reverse = $LinkObject->PossibleTypesList( Object1 => 'ITSMConfigItem', Object2 => 'Ticket' );
    $Out{link_types} = { types => \%T, possible => \%Pairs, possible_reverse_order_ci_ticket => [ sort keys %Reverse ] };
}

# --- 6. permissions ----------------------------------------------------------------------------
{
    my $CI = _NewCI( Name => "$Prefix-perm.example.org", Depl => 'Production', Inci => 'Operational' );
    my $T  = $TicketObject->TicketCreate(
        Title => "$Prefix permission ticket", Queue => 'Raw', Lock => 'unlock', Priority => '3 normal', State => 'new',
        OwnerID => 1, UserID => 1,
    ) or die 'TicketCreate (permission) failed';
    $ConfigObject->Set( Key => 'CheckEmailAddresses', Value => 0 );
    $ConfigObject->Set( Key => 'CheckMXRecord',      Value => 0 );
    my $UserID = $UserObject->UserAdd(
        UserFirstname => 'Itsm', UserLastname => 'Behaviour', UserLogin => "$Prefix-agent",
        UserEmail => "$Prefix-agent\@example.org", ValidID => 1, ChangeUserID => 1,
    ) or die 'UserAdd failed';
    my $UsersGroup = $GroupObject->GroupLookup( Group => 'users' );
    my $CIGroup    = $GroupObject->GroupLookup( Group => 'itsm-configitem' );
    my $Class      = $GCObject->ItemGet( Class => 'ITSM::ConfigItem::Class', Name => 'Computer' );
    my $Check = sub {
        return {
            ci     => ( $LinkObject->ObjectPermission( Object => 'ITSMConfigItem', Key => $CI, UserID => $UserID ) ? 1 : 0 ),
            ticket => ( $LinkObject->ObjectPermission( Object => 'Ticket',         Key => $T,  UserID => $UserID ) ? 1 : 0 ),
        };
    };
    my %P;
    $P{class_permission_group_id}   = $Class->{Permission} + 0;
    $P{itsm_configitem_group_id}    = $CIGroup + 0;
    $P{zoom_permission_type}        = $ConfigObject->Get('ITSMConfigItem::Frontend::AgentITSMConfigItemZoom')->{Permission};
    $P{no_groups}                   = $Check->();
    $GroupObject->PermissionGroupUserAdd( GID => $UsersGroup, UID => $UserID, Permission => { ro => 1, rw => 0 }, UserID => 1 );
    $P{users_ro}                    = $Check->();
    $GroupObject->PermissionGroupUserAdd( GID => $CIGroup, UID => $UserID, Permission => { ro => 0, rw => 1 }, UserID => 1 );
    $P{users_ro_cigroup_rw_only}    = $Check->();
    $GroupObject->PermissionGroupUserAdd( GID => $CIGroup, UID => $UserID, Permission => { ro => 1, rw => 0 }, UserID => 1 );
    $P{users_ro_cigroup_ro_only}    = $Check->();
    $GroupObject->PermissionGroupUserAdd( GID => $UsersGroup, UID => $UserID, Permission => { ro => 0, rw => 1 }, UserID => 1 );
    $P{users_rw_only_cigroup_ro}    = $Check->();
    $Out{permissions} = \%P;
}


# --- 7a. definition rules: _DefinitionPrepare defaults and DefinitionCheck verdicts --------------
{
    my @Cases = (
        [ 'empty',             {} ],
        [ 'min0_max10',        { CountMin => 0, CountMax => 10 } ],
        [ 'max0',              { CountMax => 0 } ],
        [ 'min_above_max',     { CountMin => 5, CountMax => 2 } ],
        [ 'min2_only',         { CountMin => 2 } ],
        [ 'default_below_min', { CountMax => 3, CountDefault => 0 } ],
        [ 'default_above_max', { CountMax => 2, CountDefault => 9 } ],
        [ 'default_zero_ok',   { CountMin => 0, CountMax => 10, CountDefault => 0 } ],
        [ 'sub_not_array',     { Sub => 'x' } ],
        [ 'sub_empty_array',   { Sub => [] } ],
        [ 'sub_child_defaults', { Sub => [ {} ] } ],
    );
    my %Prepare;
    for my $Case (@Cases) {
        my $Item = $Case->[1];
        $CIObject->_DefinitionPrepare( DefinitionRef => [$Item] );
        my %Result = map { $_ => $Item->{$_} + 0 } grep { defined $Item->{$_} } qw(CountMin CountMax CountDefault);
        $Result{has_sub} = exists $Item->{Sub} ? 1 : 0;
        if ( ref $Item->{Sub} eq 'ARRAY' && @{ $Item->{Sub} } ) {
            my $Child = $Item->{Sub}[0];
            $Result{sub_child} = { map { $_ => $Child->{$_} + 0 } qw(CountMin CountMax CountDefault) };
        }
        $Prepare{ $Case->[0] } = \%Result;
    }
    my %Check;
    my %Definitions = (
        valid_yaml      => "---\n- Key: A\n  Input:\n    Type: Text\n",
        key_with_space  => "---\n- Key: 'A B'\n",
        key_non_ascii   => "---\n- Key: \"\\xC4\"\n",
        legacy_perl     => "[ { Key => 'A', Input => { Type => 'Text' } } ]",
        not_a_list      => "---\nKey: A\n",
        sub_key_space   => "---\n- Key: A\n  Sub:\n  - Key: 'B C'\n",
        sub_valid       => "---\n- Key: A\n  Sub:\n  - Key: B\n",
    );
    for my $Name ( sort keys %Definitions ) {
        $Check{$Name} = $CIObject->DefinitionCheck( Definition => $Definitions{$Name} ) ? 1 : 0;
    }
    my $TypeDir = '/opt/otrs/Kernel/System/ITSMConfigItem/XML/Type';
    opendir( my $DH, $TypeDir ) or die "cannot read $TypeDir";
    my @Types = sort map { s/\.pm$//r } grep { /\.pm$/ } readdir($DH);
    closedir($DH);
    $Out{definition_rules} = { prepare => \%Prepare, check => \%Check, input_type_modules => \@Types };
}

# --- 7/8. Package data: ticket types / states / defaults ---------------------------------------
{
    my %Types = reverse $Kernel::OM->Get('Kernel::System::Type')->TypeList( Valid => 0 );
    $Out{ticket_types} = [ sort keys %Types ];
    my $StateObject = $Kernel::OM->Get('Kernel::System::State');
    my %State = $StateObject->StateGet( Name => 'closed with workaround' );
    $Out{closed_with_workaround} = { id => $State{ID} + 0, type_id => $State{TypeID} + 0, type => $State{TypeName}, valid_id => $State{ValidID} + 0 };
}

print "GOLDEN_ITSM_BEHAVIOUR_BEGIN\n", $JSON->Encode( Data => \%Out ), "\nGOLDEN_ITSM_BEHAVIOUR_END\n";
