# --
# Copyright (C) 2026 Cygnus Networks GmbH, https://cygnusnetworks.de/
# --
# This add-on runs inside Znuny and uses Znuny's GPL-3.0-licensed APIs;
# it is therefore licensed under GPL-3.0-only (unlike the rest of the
# Tiqora project, which is AGPL-3.0-only). This software comes with
# ABSOLUTELY NO WARRANTY. For details, see the enclosed LICENSE file.
# If you did not receive it, see https://www.gnu.org/licenses/gpl-3.0.txt.
# --

package Kernel::Modules::AgentTiqoraITSMLink;

use strict;
use warnings;
use utf8;

use HTTP::Request;
use LWP::UserAgent;

our $ObjectManagerDisabled = 1;

=head1 NAME

Kernel::Modules::AgentTiqoraITSMLink - link a ticket to a CI of Tiqora (AJAX)

=head1 DESCRIPTION

Optional. Does nothing unless C<TiqoraITSM::Linking::Enabled> is on, the add-on
is active (Tiqora holds the ITSM data) and C<TiqoraITSM::APIKey> is set.

A state-changing AJAX action, therefore: C<POST> only and the challenge token
is checked first (C<ChallengeTokenCheck>). The agent needs C<rw> on the ticket
and read access to the group of the CI's class; Tiqora checks the rights of
the key user again and the add-on does not weaken them. The request to Tiqora is
C<POST E<lt>BaseURLE<gt>/api/v1/cmdb/links> with the body C<{source_kind: "ci",
source_key, target_kind: "ticket", target_key, link_type}> and the header
C<Authorization: Bearer E<lt>keyE<gt>>. The key is never logged, never part of
a message and never output. Answers JSON C<{Success, Message}>.

=cut

sub new {
    my ( $Type, %Param ) = @_;

    my $Self = {%Param};
    bless( $Self, $Type );

    return $Self;
}

sub Run {
    my ( $Self, %Param ) = @_;

    my $LayoutObject = $Kernel::OM->Get('Kernel::Output::HTML::Layout');

    # A state-changing action must check the challenge token itself (exits on failure).
    $LayoutObject->ChallengeTokenCheck();

    my ( $Success, $Message ) = eval { $Self->_Link() };
    if ($@) {
        $Success = 0;
        $Message = 'Die Verknüpfung konnte nicht angelegt werden.';
    }

    return $Self->_Reply( Success => $Success ? 1 : 0, Message => $Message // '' );
}

sub _Reply {
    my ( $Self, %Param ) = @_;

    my $JSON = $Kernel::OM->Get('Kernel::System::JSON')->Encode(
        Data => {
            Success => $Param{Success},
            Message => $Param{Message},
        },
    );

    return $Kernel::OM->Get('Kernel::Output::HTML::Layout')->Attachment(
        ContentType => 'application/json; charset=utf8',
        Content     => $JSON,
        Type        => 'inline',
        NoCache     => 1,
    );
}

sub _Link {
    my ( $Self, %Param ) = @_;

    my $ConfigObject = $Kernel::OM->Get('Kernel::Config');
    my $AddonObject  = $Kernel::OM->Get('Kernel::System::TiqoraITSM');
    my $ParamObject  = $Kernel::OM->Get('Kernel::System::Web::Request');
    my $LogObject    = $Kernel::OM->Get('Kernel::System::Log');

    return ( 0, 'Nur POST ist erlaubt.' ) if ( $ENV{REQUEST_METHOD} // '' ) ne 'POST';

    return ( 0, 'Das Verknüpfen mit CIs ist nicht eingeschaltet.' )
        if !$ConfigObject->Get('TiqoraITSM::Linking::Enabled') || !$AddonObject->Active();

    my $Key = $ConfigObject->Get('TiqoraITSM::APIKey');
    return ( 0, 'Der API-Schlüssel für Tiqora ist nicht gesetzt.' ) if !defined $Key || !length $Key;

    my $TicketID = $ParamObject->GetParam( Param => 'TicketID' ) // '';
    my $CIID     = $ParamObject->GetParam( Param => 'CIID' )     // '';
    my $LinkType = $ParamObject->GetParam( Param => 'LinkType' ) // '';
    $LinkType = 'RelevantTo' if !length $LinkType;

    for ( $TicketID, $CIID ) {
        s/^\s+|\s+$//g;
    }
    return ( 0, 'Ticket und CI müssen als Nummern angegeben werden.' )
        if $TicketID !~ m/^\d{1,18}$/ || $CIID !~ m/^\d{1,18}$/;
    return ( 0, 'Ungültiger Verknüpfungstyp.' ) if $LinkType !~ m/^[\w.\- ]{1,50}$/;

    my $UserID = $Self->{UserID};

    my $TicketAllowed = $Kernel::OM->Get('Kernel::System::Ticket')->TicketPermission(
        Type     => 'rw',
        TicketID => $TicketID,
        UserID   => $UserID,
        LogNo    => 1,
    );
    return ( 0, 'Sie haben keine Schreibrechte auf dieses Ticket.' ) if !$TicketAllowed;

    return ( 0, 'Sie dürfen dieses CI nicht sehen.' )
        if !$AddonObject->CIReadable( CIID => $CIID, UserID => $UserID );

    my $Base = $ConfigObject->Get('TiqoraITSM::BaseURL') // '';
    $Base =~ s/^\s+|\s+$//g;
    return ( 0, 'Die Adresse von Tiqora ist nicht gesetzt.' ) if $Base !~ m{^https?://[^\s<>"']+$}i;
    $Base =~ s{/+$}{};

    my $Body = $Kernel::OM->Get('Kernel::System::JSON')->Encode(
        Data => {
            source_kind => 'ci',
            source_key  => "$CIID",
            target_kind => 'ticket',
            target_key  => "$TicketID",
            link_type   => $LinkType,
        },
    );

    # Kernel::System::WebUserAgent can only send form data, not a JSON body.
    my $UserAgent = LWP::UserAgent->new();
    $UserAgent->timeout( $ConfigObject->Get('WebUserAgent::Timeout') || 15 );
    $UserAgent->agent( 'TiqoraITSM/1.0' );
    if ( $ConfigObject->Get('WebUserAgent::DisableSSLVerification') ) {
        $UserAgent->ssl_opts( verify_hostname => 0 );
    }
    if ( my $Proxy = $ConfigObject->Get('WebUserAgent::Proxy') ) {
        $UserAgent->proxy( [ 'http', 'https' ], $Proxy );
    }

    my $Request = HTTP::Request->new(
        'POST',
        "$Base/api/v1/cmdb/links",
        [
            'Authorization' => "Bearer $Key",
            'Content-Type'  => 'application/json',
            'Accept'        => 'application/json',
        ],
        $Body,
    );
    my $Response = $UserAgent->request($Request);

    return ( 1, 'Das Ticket wurde mit dem CI verknüpft.' ) if $Response->is_success();

    # Never log the request (it carries the key); the status line is enough.
    $LogObject->Log(
        Priority => 'notice',
        Message  => 'TiqoraITSM: linking ticket ' . $TicketID . ' to CI ' . $CIID . ' failed: ' . $Response->code(),
    );

    my $Code = $Response->code();
    return ( 0, 'Diese Verknüpfung besteht bereits.' )                  if $Code == 409;
    return ( 0, 'Tiqora hat die Verknüpfung abgelehnt (keine Berechtigung).' ) if $Code == 401 || $Code == 403;
    return ( 0, 'Tiqora hat die Verknüpfung abgelehnt.' )              if $Code >= 400 && $Code < 500;

    return ( 0, 'Tiqora ist nicht erreichbar.' );
}

1;
