# --
# Copyright (C) 2026 Cygnus Networks GmbH, https://cygnusnetworks.de/
# --
# This add-on runs inside Znuny and uses Znuny's GPL-3.0-licensed APIs;
# it is therefore licensed under GPL-3.0-only (unlike the rest of the
# Tiqora project, which is AGPL-3.0-only). This software comes with
# ABSOLUTELY NO WARRANTY. For details, see the enclosed LICENSE file.
# If you did not receive it, see https://www.gnu.org/licenses/gpl-3.0.txt.
# --

package Kernel::Output::HTML::TicketZoom::TiqoraITSM;

use parent 'Kernel::Output::HTML::Base';

use strict;
use warnings;
use utf8;

our $ObjectManagerDisabled = 1;

=head1 NAME

Kernel::Output::HTML::TicketZoom::TiqoraITSM - sidebar widget "Betroffene CIs (Tiqora)"

=head1 DESCRIPTION

Shows the configuration items that Tiqora's CMDB links to the ticket, each with
its deployment and incident state and a link into Tiqora. The widget returns
nothing (and is not rendered) when the add-on is inactive (Tiqora does not
hold the ITSM data), when no CI is linked, when the agent may not read any of
the linked CIs, or when anything goes wrong.

=cut

sub Run {
    my ( $Self, %Param ) = @_;

    my $Output = eval { $Self->_Render(%Param) };

    return if !defined $Output || !length $Output;

    my $Config = $Param{Config} // {};
    my %Rank;
    %Rank = ( Rank => $Config->{Rank} ) if exists $Config->{Rank} && defined $Config->{Rank};

    return {
        Output => $Output,
        %Rank,
    };
}

sub _Render {
    my ( $Self, %Param ) = @_;

    my $TicketID = $Param{Ticket} ? $Param{Ticket}->{TicketID} : undef;
    return if !$TicketID;

    my $CIs = $Kernel::OM->Get('Kernel::System::TiqoraITSM')->TicketCIs(
        TicketID => $TicketID,
        UserID   => $Self->{UserID},
    );
    return if !@{ $CIs // [] };

    my $LayoutObject = $Kernel::OM->Get('Kernel::Output::HTML::Layout');

    my %Dot = (
        operational => 'Operational',
        warning     => 'Warning',
        incident    => 'Incident',
    );

    for my $CI ( @{$CIs} ) {
        $LayoutObject->Block(
            Name => 'CI',
            Data => {
                %{$CI},
                DeplClass => $Dot{ lc( $CI->{DeplFunctionality} // '' ) } // 'Unknown',
                InciClass => $Dot{ lc( $CI->{InciFunctionality} // '' ) } // 'Unknown',
            },
        );
    }

    return $LayoutObject->Output(
        TemplateFile => 'AgentTicketZoom/TiqoraITSM',
        Data         => {},
    );
}

1;
