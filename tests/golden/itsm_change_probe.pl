#!/usr/bin/perl
# Read-only probe of the ITSMChangeManagement package on a golden ITSM peer (wave-5 itsm-change,
# Task 1). Run AFTER itsm_seed.pl (the three `chg94` changes must exist); copied in and run by
# `tests/golden/itsm_survey.py --change-behaviour`. Prints one JSON block between
# GOLDEN_ITSM_CHANGE_BEGIN / GOLDEN_ITSM_CHANGE_END.
#
# It answers, with the package's own API, what the source reading in the vectors claims:
#   * which link types the LinkObject allows for ITSMChange / ITSMWorkOrder, and whether a
#     change <-> configuration item link can be created at all (negative probe, rolled back);
#   * how ChangeGet derives the change's planned/actual start/end from the work orders;
#   * which history rows the event modules write (type, fieldname, content_old/new);
#   * description vs description_plain, the CAB, the dynamic field object types and the format of
#     change_template.content.
use strict;
use warnings;
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;

local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => { LogPrefix => 'golden-itsm-change-probe' },
);

my $ChangeObject = $Kernel::OM->Get('Kernel::System::ITSMChange');
my $WOObject     = $Kernel::OM->Get('Kernel::System::ITSMChange::ITSMWorkOrder');
my $HistoryObject = $Kernel::OM->Get('Kernel::System::ITSMChange::History');
my $TemplateObject = $Kernel::OM->Get('Kernel::System::ITSMChange::Template');
my $LinkObject   = $Kernel::OM->Get('Kernel::System::LinkObject');
my $DFObject     = $Kernel::OM->Get('Kernel::System::DynamicField');
my $DBObject     = $Kernel::OM->Get('Kernel::System::DB');
my $JSON         = $Kernel::OM->Get('Kernel::System::JSON');

sub _Titles {
    my %ID;
    for my $Title ( 'chg94 Mail server replacement', 'chg94 Switch firmware rollout', 'chg94 Database migration' ) {
        my $Found = $ChangeObject->ChangeSearch( ChangeTitle => $Title, UserID => 1 ) || [];
        die "seeded change '$Title' not found: run itsm_seed.pl first" if @{$Found} != 1;
        $ID{$Title} = $Found->[0];
    }
    return \%ID;
}
my $IDs = _Titles();
my %Key = (
    mail     => $IDs->{'chg94 Mail server replacement'},
    firmware => $IDs->{'chg94 Switch firmware rollout'},
    database => $IDs->{'chg94 Database migration'},
);

my %Result;

# --- 1: link objects and possible link types ---------------------------------------------------
my %Possible;
for my $Pair (
    [qw(ITSMChange Ticket)],        [qw(ITSMChange ITSMConfigItem)], [qw(ITSMChange Service)],
    [qw(ITSMChange ITSMWorkOrder)], [qw(ITSMChange ITSMChange)],
    [qw(ITSMWorkOrder Ticket)],     [qw(ITSMWorkOrder ITSMConfigItem)], [qw(ITSMWorkOrder Service)],
    [qw(ITSMWorkOrder ITSMWorkOrder)],
    )
{
    my %Types = $LinkObject->PossibleTypesList( Object1 => $Pair->[0], Object2 => $Pair->[1] );
    $Possible{ $Pair->[0] . '/' . $Pair->[1] } = [ sort keys %Types ];
}
my %Objects = $LinkObject->PossibleObjectsList( Object => 'ITSMChange', UserID => 1 );
my %WOObjects = $LinkObject->PossibleObjectsList( Object => 'ITSMWorkOrder', UserID => 1 );

# Negative probe: the default configuration has no ITSMChange <-> ITSMConfigItem link type.
my $CIs = $Kernel::OM->Get('Kernel::System::ITSMConfigItem')->ConfigItemSearchExtended( Name => 'web01.example.org' ) || [];
my $ChangeToCI = $CIs->[0]
    ? ( $LinkObject->LinkAdd(
        SourceObject => 'ITSMChange', SourceKey => $Key{firmware},
        TargetObject => 'ITSMConfigItem', TargetKey => $CIs->[0],
        Type => 'RelevantTo', State => 'Valid', UserID => 1,
    ) ? 1 : 0 )
    : undef;
$Result{links} = {
    possible_types      => \%Possible,
    possible_objects    => [ sort keys %Objects ],
    possible_objects_wo => [ sort keys %WOObjects ],
    change_to_ci_link_add_succeeded => $ChangeToCI,
};

# --- 2: derived times ---------------------------------------------------------------------------
my %Times;
for my $Name ( sort keys %Key ) {
    my $Change = $ChangeObject->ChangeGet( ChangeID => $Key{$Name}, UserID => 1 );
    my %Raw;
    $DBObject->Prepare(
        SQL => 'SELECT MIN(planned_start_time), MAX(planned_end_time), MIN(actual_start_time), MAX(actual_end_time) '
            . 'FROM change_workorder WHERE change_id = ?',
        Bind => [ \$Key{$Name} ],
    );
    while ( my @Row = $DBObject->FetchrowArray() ) {
        @Raw{qw(min_planned_start max_planned_end min_actual_start max_actual_end)} = @Row;
    }
    my @WO;
    for my $WOID ( @{ $Change->{WorkOrderIDs} } ) {
        my $WO = $WOObject->WorkOrderGet( WorkOrderID => $WOID, UserID => 1 );
        push @WO, {
            number => $WO->{WorkOrderNumber} + 0, type => $WO->{WorkOrderType}, state => $WO->{WorkOrderState},
            planned_start => $WO->{PlannedStartTime}, planned_end => $WO->{PlannedEndTime},
            actual_start => $WO->{ActualStartTime}, actual_end => $WO->{ActualEndTime},
        };
    }
    $Times{$Name} = {
        state            => $Change->{ChangeState},
        planned_start    => $Change->{PlannedStartTime},
        planned_end      => $Change->{PlannedEndTime},
        actual_start     => $Change->{ActualStartTime},
        actual_end       => $Change->{ActualEndTime},
        planned_effort   => $Change->{PlannedEffort},
        accounted_time   => $Change->{AccountedTime},
        workorder_count  => $Change->{WorkOrderCount} + 0,
        raw_min_max      => \%Raw,
        workorders       => \@WO,
    };
}
$Result{times} = \%Times;

# --- 3: history ---------------------------------------------------------------------------------
my %History;
for my $Name ( sort keys %Key ) {
    my $Entries = $HistoryObject->ChangeHistoryGet( ChangeID => $Key{$Name}, UserID => 1 ) || [];
    my @Rows = map {
        {
            type => $_->{HistoryType}, fieldname => $_->{Fieldname},
            content_old => $_->{ContentOld}, content_new => $_->{ContentNew},
            workorder_id => $_->{WorkOrderID},
        }
    } @{$Entries};
    $History{$Name} = \@Rows;
}
$Result{history} = \%History;

# --- 4/6: text columns, CAB, dynamic fields ----------------------------------------------------
my %Texts;
for my $Name ( sort keys %Key ) {
    my $Change = $ChangeObject->ChangeGet( ChangeID => $Key{$Name}, UserID => 1 );
    $Texts{$Name} = {
        description        => $Change->{Description},
        description_plain  => $Change->{DescriptionPlain},
        justification      => $Change->{Justification},
        justification_plain => $Change->{JustificationPlain},
        cab_agents         => $Change->{CABAgents},
        cab_customers      => $Change->{CABCustomers},
        dynamic_fields     => { map { $_ => $Change->{$_} } grep { /^DynamicField_/ } keys %{$Change} },
    };
}
my $MailWO = ( $ChangeObject->ChangeGet( ChangeID => $Key{mail}, UserID => 1 ) )->{WorkOrderIDs}[0];
my $WO1 = $WOObject->WorkOrderGet( WorkOrderID => $MailWO, UserID => 1 );
$Texts{workorder} = {
    instruction => $WO1->{Instruction}, instruction_plain => $WO1->{InstructionPlain},
    report => $WO1->{Report}, report_plain => $WO1->{ReportPlain},
    dynamic_fields => { map { $_ => $WO1->{$_} } grep { /^DynamicField_/ } keys %{$WO1} },
};
$Result{texts} = \%Texts;
$Result{dynamic_field_object_types} = {
    map { $_ => [ sort map { $_->{Name} } @{ $DFObject->DynamicFieldListGet( ObjectType => $_ ) || [] } ] }
        qw(ITSMChange ITSMWorkOrder)
};

# --- 7: template content --------------------------------------------------------------------------
my $TemplateIDs = $TemplateObject->TemplateSearch( TemplateName => 'chg94 template firmware', UserID => 1 ) || [];
my $Template = @{$TemplateIDs} ? $TemplateObject->TemplateGet( TemplateID => $TemplateIDs->[0], UserID => 1 ) : undef;
$Result{template} = $Template ? {
    type           => $Template->{Type},
    content_prefix => substr( $Template->{Content}, 0, 120 ),
    content_is_perl_dumper => ( $Template->{Content} =~ m{\A\$VAR1\s*=\s*\{} ? 1 : 0 ),
    content_is_storable    => ( substr( $Template->{Content}, 0, 4 ) eq "pst0" ? 1 : 0 ),
    content_length_positive => ( length $Template->{Content} ? 1 : 0 ),
} : undef;

# --- 8: groups --------------------------------------------------------------------------------------
my $GroupObject = $Kernel::OM->Get('Kernel::System::Group');
$Result{groups} = { map { $_ => ( $GroupObject->GroupLookup( Group => $_ ) ? 1 : 0 ) } qw(itsm-change itsm-change-builder itsm-change-manager) };
my $Agent = $Kernel::OM->Get('Kernel::System::User')->UserLookup( UserLogin => 'chg94.nogroup', Silent => 1 );
$Result{permission} = {
    root_ro => $ChangeObject->Permission( Type => 'ro', Action => 'AgentITSMChangeZoom', ChangeID => $Key{mail}, UserID => 1 ) ? 1 : 0,
    agent_without_group_ro => $Agent ? ( $ChangeObject->Permission( Type => 'ro', Action => 'AgentITSMChangeZoom', ChangeID => $Key{mail}, UserID => $Agent ) ? 1 : 0 ) : undef,
};

print "GOLDEN_ITSM_CHANGE_BEGIN\n" . $JSON->Encode( Data => \%Result ) . "\nGOLDEN_ITSM_CHANGE_END\n";
