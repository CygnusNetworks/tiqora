# --
# Copyright (C) 2026 Cygnus Networks GmbH, https://cygnusnetworks.de/
# --
# This software comes with ABSOLUTELY NO WARRANTY. For details, see
# the enclosed LICENSE file (GPL-3.0-only).
# --

package Kernel::Language::de_TiqoraITSM;

use strict;
use warnings;
use utf8;

sub Data {
    my $Self = shift;

    # TT Template: Kernel/Output/HTML/Templates/Standard/AgentTicketZoom/TiqoraITSM.tt
    # (the widget title is German in the template itself and needs no translation)
    $Self->{Translation}->{'Deployment State'} = 'Verwendungsstatus';
    $Self->{Translation}->{'Incident State'}   = 'Vorfallstatus';

    # SysConfig
    $Self->{Translation}->{'Address of the Tiqora installation, without a trailing path (for example https://tiqora.example.org). Links from Znuny to Tiqora are built from it.'} =
        'Adresse der Tiqora-Installation ohne Pfad (zum Beispiel https://tiqora.example.org). Daraus werden die Verweise von Znuny nach Tiqora gebildet.';
    $Self->{Translation}->{'Path of a configuration item in the Tiqora agent interface; the CI id is appended.'} =
        'Pfad eines Konfigurationselements in der Tiqora-Agentenoberfläche; die CI-ID wird angehängt.';
    $Self->{Translation}->{'Path of a configuration item in the Tiqora customer portal; the CI id is appended.'} =
        'Pfad eines Konfigurationselements im Tiqora-Kundenportal; die CI-ID wird angehängt.';
    $Self->{Translation}->{'Treat the CMDB as read-only in Znuny even when Tiqora has not set the flag.'} =
        'Die CMDB in Znuny auch dann schreibgeschützt behandeln, wenn Tiqora die Markierung nicht gesetzt hat.';
    $Self->{Translation}->{'Show the widget with the CIs Tiqora links to the ticket in the ticket zoom of the agent interface.'} =
        'Zeigt in der Ticketansicht der Agentenoberfläche die Widget-Box mit den CIs, die Tiqora mit dem Ticket verknüpft.';

    return 1;
}

1;
