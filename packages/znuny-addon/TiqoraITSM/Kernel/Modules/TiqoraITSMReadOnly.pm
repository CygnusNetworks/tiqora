# --
# Copyright (C) 2026 Cygnus Networks GmbH, https://cygnusnetworks.de/
# --
# This add-on runs inside Znuny and uses Znuny's GPL-3.0-licensed APIs;
# it is therefore licensed under GPL-3.0-only (unlike the rest of the
# Tiqora project, which is AGPL-3.0-only). This software comes with
# ABSOLUTELY NO WARRANTY. For details, see the enclosed LICENSE file.
# If you did not receive it, see https://www.gnu.org/licenses/gpl-3.0.txt.
# --

package Kernel::Modules::TiqoraITSMReadOnly;

use strict;
use warnings;
use utf8;

our $ObjectManagerDisabled = 1;

=head1 NAME

Kernel::Modules::TiqoraITSMReadOnly - PreApplication guard for the CI screens

=head1 DESCRIPTION

Registered as C<PreApplicationModule###TiqoraITSMReadOnly>. Once the CMDB moved
to Tiqora (C<itsm.znuny_readonly> is C<1>, or C<TiqoraITSM::ReadOnly::Force> is
on), Znuny's own screens that add, edit or delete configuration items (and the
class definition editor) answer with a message instead. Read screens (zoom,
search, history, print, attachment download) stay open. Never writes to the
database, never dies: on any problem the request goes on unchanged.

=cut

sub new {
    my ( $Type, %Param ) = @_;

    my $Self = {%Param};
    bless( $Self, $Type );

    return $Self;
}

sub PreRun {
    my ( $Self, %Param ) = @_;

    my $Output = eval { $Self->_Guard() };

    return $Output if defined $Output && length $Output;
    return;
}

sub _Guard {
    my ( $Self, %Param ) = @_;

    my $Action = $Self->{Action} // '';
    return if !length $Action;

    my $AddonObject = $Kernel::OM->Get('Kernel::System::TiqoraITSM');
    return if !$AddonObject->ReadOnly();

    my $ParamObject = $Kernel::OM->Get('Kernel::System::Web::Request');

    my $Subaction = $Self->{Subaction};
    $Subaction = $ParamObject->GetParam( Param => 'Subaction' ) if !defined $Subaction;
    $Subaction //= '';

    my @LinkedObjects;
    if ( $Action eq 'AgentLinkObject' ) {
        for my $Name (qw(SourceObject TargetObject TargetIdentifier)) {
            my $Value = $Self->{$Name} // $ParamObject->GetParam( Param => $Name );
            push @LinkedObjects, $Value if defined $Value;
        }
    }

    return if !$AddonObject->ActionBlocked(
        Action        => $Action,
        Subaction     => $Subaction,
        LinkedObjects => \@LinkedObjects,
    );

    my $LayoutObject = $Kernel::OM->Get('Kernel::Output::HTML::Layout');
    my $BaseURL      = $Kernel::OM->Get('Kernel::Config')->Get('TiqoraITSM::BaseURL') // '';
    $BaseURL = '' if $BaseURL !~ m{^https?://[^\s<>"']+$}i;

    my $Message = 'Die CMDB wird jetzt in Tiqora gepflegt.';
    my $Comment = 'Konfigurationselemente können in Znuny nur noch angesehen werden.'
        . ( length $BaseURL ? " Bitte ändern Sie sie in Tiqora: $BaseURL" : '' );

    # A plain warning page: no error is logged for a deliberate refusal.
    my $Output = $LayoutObject->Header( Title => 'Warning' );
    $Output .= $LayoutObject->NavigationBar() if $Self->{UserID};
    $Output .= $LayoutObject->Warning(
        Message => $Message,
        Comment => $Comment,
    );
    $Output .= $LayoutObject->Footer();

    return $Output;
}

1;
