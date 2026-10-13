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

    # TT Template: Kernel/Output/HTML/Templates/Standard/AgentTicketZoom/TiqoraITSM.tt
    $Self->{Translation}->{'CI-ID'} = 'CI-ID';

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

    $Self->{Translation}->{'Znuny screens for configuration items that are refused while the CMDB is read-only. An entry is an action name (the whole screen) or Action::Subaction (one subaction). Read screens (zoom, search, history, print, attachment download) are not listed. AgentLinkObject is guarded by the module itself: it refuses to link to or from configuration items.'} =
        'Znuny-Masken für Konfigurationselemente, die abgewiesen werden, solange die CMDB schreibgeschützt ist. Ein Eintrag ist ein Aktionsname (die ganze Maske) oder Aktion::Unteraktion (eine Unteraktion). Lesemasken (Zoom, Suche, Verlauf, Druck, Anhang-Download) stehen nicht in der Liste. AgentLinkObject schützt das Modul selbst: Es verweigert Verknüpfungen von und zu Konfigurationselementen.';
    $Self->{Translation}->{'Refuses the Znuny screens that change configuration items once the CMDB is read-only because Tiqora holds it (TiqoraITSM::ReadOnly::Actions).'} =
        'Weist die Znuny-Masken ab, die Konfigurationselemente ändern, sobald die CMDB schreibgeschützt ist, weil Tiqora sie führt (TiqoraITSM::ReadOnly::Actions).';
    $Self->{Translation}->{'Frontend module registration for linking a ticket to a configuration item of Tiqora (AJAX). Does nothing unless TiqoraITSM::Linking::Enabled is on.'} =
        'Modulregistrierung zum Verknüpfen eines Tickets mit einem Konfigurationselement von Tiqora (AJAX). Tut nichts, solange TiqoraITSM::Linking::Enabled aus ist.';
    $Self->{Translation}->{'Link CI (Tiqora)'} = 'CI verknüpfen (Tiqora)';
    $Self->{Translation}->{'Frontend module registration for the customer page with the configuration items of Tiqora.'} =
        'Modulregistrierung für die Kundenseite mit den Konfigurationselementen aus Tiqora.';
    $Self->{Translation}->{'Configuration items of the customer (from Tiqora).'} = 'Konfigurationselemente des Kunden (aus Tiqora).';
    $Self->{Translation}->{'Configuration items'} = 'Konfigurationselemente';
    $Self->{Translation}->{'Configuration items.'} = 'Konfigurationselemente.';
    $Self->{Translation}->{'Main menu item registration for the customer page with the configuration items of Tiqora.'} =
        'Registrierung des Hauptmenüeintrags für die Kundenseite mit den Konfigurationselementen aus Tiqora.';

    return 1;
}

1;
