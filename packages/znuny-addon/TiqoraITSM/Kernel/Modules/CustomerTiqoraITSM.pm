# --
# Copyright (C) 2026 Cygnus Networks GmbH, https://cygnusnetworks.de/
# --
# This add-on runs inside Znuny and uses Znuny's GPL-3.0-licensed APIs;
# it is therefore licensed under GPL-3.0-only (unlike the rest of the
# Tiqora project, which is AGPL-3.0-only). This software comes with
# ABSOLUTELY NO WARRANTY. For details, see the enclosed LICENSE file.
# If you did not receive it, see https://www.gnu.org/licenses/gpl-3.0.txt.
# --

package Kernel::Modules::CustomerTiqoraITSM;

use strict;
use warnings;
use utf8;

our $ObjectManagerDisabled = 1;

=head1 NAME

Kernel::Modules::CustomerTiqoraITSM - the customer's configuration items (from Tiqora)

=head1 DESCRIPTION

A customer interface page listing the CIs that Tiqora links to the logged-in
customer user or to the customer's companies, restricted to classes that are
visible in Tiqora's customer portal. Header fields only (number, name, class,
states), each with a link to the CI in Tiqora's portal. Registered but disabled
(C<Valid="0">) in the SysConfig; enable it together with the navigation entry.
Never writes to the database.

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

    my $Content = $Self->Content();

    my $Output = $LayoutObject->CustomerHeader( Title => 'Konfigurationselemente' );
    $Output .= $LayoutObject->CustomerNavigationBar();
    $Output .= $Content;
    $Output .= $LayoutObject->CustomerFooter();

    return $Output;
}

=head2 Content()

The page body (HTML) for C<UserLogin> / C<UserCustomerID> of the object (set by
the customer frontend) or the given C<CustomerUserID> / C<CustomerID>.

=cut

sub Content {
    my ( $Self, %Param ) = @_;

    my $LayoutObject = $Kernel::OM->Get('Kernel::Output::HTML::Layout');

    my $CustomerUserID = $Param{CustomerUserID} // $Self->{UserLogin}      // '';
    my $CustomerID     = $Param{CustomerID}     // $Self->{UserCustomerID} // '';

    my $CIs = $Kernel::OM->Get('Kernel::System::TiqoraITSM')->CustomerCIs(
        CustomerUserID => $CustomerUserID,
        CustomerID     => $CustomerID,
    );

    my %Dot = (
        operational => 'Operational',
        warning     => 'Warning',
        incident    => 'Incident',
    );

    for my $CI ( @{ $CIs // [] } ) {
        $LayoutObject->Block(
            Name => 'CI',
            Data => {
                %{$CI},
                DeplClass => $Dot{ lc( $CI->{DeplFunctionality} // '' ) } // 'Unknown',
                InciClass => $Dot{ lc( $CI->{InciFunctionality} // '' ) } // 'Unknown',
            },
        );
    }
    $LayoutObject->Block( Name => 'NoCIs' ) if !@{ $CIs // [] };

    return $LayoutObject->Output(
        TemplateFile => 'CustomerTiqoraITSM',
        Data         => {},
    );
}

1;
