# ruff: noqa: E501 - the embedded Perl scenario keeps its natural line length
"""Shared helpers of the ITSM importer golden modules (wave-4 import Task 28).

``test_itsm_import.py`` and ``test_gi_config_items.py`` both create the same kind of Znuny ITSM
data through the REAL ``Kernel::System::ITSMConfigItem`` API of the peer (so the XML storage is
what Znuny really writes), run the importer on it and compare Tiqora with Znuny. This module
holds what they share:

* :func:`create_scenario` - one Perl script: the custom class ``GoldImpAppliance`` (nested
  ``Sub`` attribute, ``CI`` reference, ``GeneralCatalog`` value, ``Integer``, ``Date``,
  ``DateTime``), 5 CIs with 1-3 versions, CI<->CI and ticket->CI links, one binary attachment;
  :func:`delete_scenario` removes it again (CIs, ticket, links).
* :func:`znuny_json` / :func:`PRELUDE` - run Perl in the peer and decode what it ``Emit``ted.
* :class:`TiqoraSnapshot` - snapshot of the ``tiqora_*`` tables the importer writes, restored in
  the teardown (the golden database is shared with the other golden modules).

All hostnames are ``*.example.org``.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

import pymysql
from _helpers import znuny_console, znuny_perl_eval
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import TiqoraItsmImportRun
from tiqora.itsm.importer.pipeline import run_import
from tiqora.itsm.importer.report import ImportReport
from tiqora.jobs.service import utc_now

CLASS_NAME = "GoldImpAppliance"
AGENT_LOGIN = "goldimp.agent"
AGENT_PASSWORD = "goldimp-agent-pw"  # noqa: S105 - throw-away peer user
AGENT_EMAIL = "goldimp.agent@golden.test"

#: written by the importer / the switch; restored after a module run
SNAPSHOT_TABLES: tuple[str, ...] = (
    "tiqora_cmdb_attachment",
    "tiqora_cmdb_ci_attr",
    "tiqora_cmdb_ci_history",
    "tiqora_cmdb_ci_version",
    "tiqora_cmdb_ci_version_archive",
    "tiqora_cmdb_ci",
    "tiqora_cmdb_class_definition",
    "tiqora_cmdb_class",
    "tiqora_cmdb_catalog",
    "tiqora_itsm_link",
    "tiqora_itsm_link_type",
    "tiqora_itsm_import_map",
    "tiqora_itsm_import_run",
    "tiqora_itsm_priority_matrix",
    "tiqora_itsm_service_meta",
)

PRELUDE = r"""
use strict;
use warnings;
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use MIME::Base64 ();
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => { LogPrefix => 'golden-itsm-import' },
);
my $JSON         = $Kernel::OM->Get('Kernel::System::JSON');
my $CIObject     = $Kernel::OM->Get('Kernel::System::ITSMConfigItem');
my $GCObject     = $Kernel::OM->Get('Kernel::System::GeneralCatalog');
my $LinkObject   = $Kernel::OM->Get('Kernel::System::LinkObject');
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $GroupObject  = $Kernel::OM->Get('Kernel::System::Group');

sub Emit { print "GOLDEN_JSON_BEGIN\n" . $JSON->Encode( Data => $_[0] ) . "\nGOLDEN_JSON_END\n"; }

# Znuny XMLData node -> { Key => [ { Content => v, SubKey => [...] }, ... ] } (the shape
# tiqora.itsm.znuny_format.xmldata.xmldata_to_attributes reads; TagKey and the leading undef of
# every instance array are dropped)
sub Plain {
    my ($Node) = @_;
    my %Out;
    for my $Key ( sort keys %{$Node} ) {
        next if $Key eq 'TagKey' || $Key eq 'Content';
        my $Instances = $Node->{$Key};
        next if ref $Instances ne 'ARRAY';
        my @List;
        for my $Index ( 1 .. $#{$Instances} ) {
            my $Child = $Instances->[$Index];
            next if ref $Child ne 'HASH';
            my $Entry = Plain($Child);
            $Entry->{Content} = defined $Child->{Content} ? "$Child->{Content}" : '';
            push @List, $Entry;
        }
        $Out{$Key} = \@List if @List;
    }
    return \%Out;
}

sub VersionPlain {
    my ($Version) = @_;
    my $Root = $Version->{XMLData} && $Version->{XMLData}->[1] ? $Version->{XMLData}->[1] : {};
    my $V    = $Root->{Version} ? $Root->{Version}->[1] : {};
    return Plain($V);
}

# the full picture of one CI: header, every version (oldest first), what Znuny returns
sub CIDump {
    my ($CIID) = @_;
    my $CI = $CIObject->ConfigItemGet( ConfigItemID => $CIID );
    my @Versions;
    for my $VersionID ( @{ $CIObject->VersionList( ConfigItemID => $CIID ) || [] } ) {
        my $V = $CIObject->VersionGet( VersionID => $VersionID, XMLDataGet => 1 );
        push @Versions, {
            id           => $VersionID + 0,
            name         => $V->{Name},
            depl_state   => $V->{DeplState},
            inci_state   => $V->{InciState},
            created      => $V->{CreateTime},
            created_by   => $V->{CreateBy} + 0,
            definition_id => $V->{DefinitionID} + 0,
            xml          => VersionPlain($V),
        };
    }
    return {
        id       => $CIID + 0,
        number   => $CI->{Number},
        class    => $CI->{Class},
        class_id => $CI->{ClassID} + 0,
        created  => $CI->{CreateTime},
        versions => \@Versions,
    };
}
"""

_DEFINITION = """\
---
- Key: Label
  Name: Label
  Searchable: 1
  Input:
    Type: Text
    Size: 50
    MaxLength: 100
    Required: 1

- Key: Support
  Name: Support
  Input:
    Type: GeneralCatalog
    Class: ITSM::ConfigItem::YesNo
    Translation: 1

- Key: Parent
  Name: Parent
  Input:
    Type: CI
    CIClassName: Computer

- Key: Installed
  Name: Installed
  Input:
    Type: Date

- Key: LastCheck
  Name: Last check
  Input:
    Type: DateTime

- Key: Port
  Name: Port
  Input:
    Type: Text
    Size: 20
    MaxLength: 20
  CountMin: 0
  CountMax: 4
  Sub:
  - Key: Speed
    Name: Speed
    Input:
      Type: Integer
      ValueMin: 1
      ValueMax: 100000
"""

#: the scenario: ``__PREFIX__`` is replaced; every name ends in ``.example.org``
_SCENARIO = r"""
my $Prefix = '__PREFIX__';

sub Reverse {
    my ($Class) = @_;
    my $List = $GCObject->ItemList( Class => $Class ) || {};
    die "general catalog class $Class is empty" if !%{$List};
    return { reverse %{$List} };
}

# --- the custom class (a general catalog item cannot be deleted: created once, kept) ----------
my $ClassIDs = Reverse('ITSM::ConfigItem::Class');
my $ClassID  = $ClassIDs->{__CLASS__};
if ( !$ClassID ) {
    $ClassID = $GCObject->ItemAdd(
        Class => 'ITSM::ConfigItem::Class', Name => '__CLASS__', ValidID => 1, UserID => 1,
    ) or die 'ItemAdd class failed';
    $ClassIDs->{__CLASS__} = $ClassID;
    my $GroupID = $GroupObject->GroupLookup( Group => 'itsm-configitem' )
        or die 'no itsm-configitem';
    $GCObject->GeneralCatalogPreferencesSet(
        ItemID => $ClassID, Key => 'Permission', Value => $GroupID,
    ) or die 'class permission failed';
}
my $Definition = $CIObject->DefinitionGet( ClassID => $ClassID );
if ( !$Definition || !$Definition->{DefinitionID} ) {
    $CIObject->DefinitionAdd(
        ClassID => $ClassID, UserID => 1, Definition => <<'GOLDEN_DEFINITION_END',
__DEFINITION__
GOLDEN_DEFINITION_END
    ) or die 'DefinitionAdd failed';
}

my $DeplIDs  = Reverse('ITSM::ConfigItem::DeploymentState');
my $InciIDs  = Reverse('ITSM::Core::IncidentState');
my $YesNoIDs = Reverse('ITSM::ConfigItem::YesNo');
my $TypeIDs  = Reverse('ITSM::ConfigItem::Computer::Type');
my ($TypeName) = sort keys %{$TypeIDs};

sub C { return [ undef, { Content => $_[0] } ] }

sub NewCI {
    my (%P) = @_;
    my $ID = $CIObject->ConfigItemAdd( ClassID => $ClassIDs->{ $P{Class} }, UserID => 1 )
        or die "ConfigItemAdd $P{Name} failed";
    my $Def = $CIObject->DefinitionGet( ClassID => $ClassIDs->{ $P{Class} } );
    for my $Version ( @{ $P{Versions} } ) {
        $CIObject->VersionAdd(
            ConfigItemID => $ID,
            Name         => $Version->{Name} || $P{Name},
            DefinitionID => $Def->{DefinitionID},
            DeplStateID  => $DeplIDs->{ $Version->{Depl} },
            InciStateID  => $InciIDs->{ $Version->{Inci} },
            XMLData      => [ undef, { Version => [ undef, $Version->{Data}( $P{Refs} ) ] } ],
            UserID       => 1,
        ) or die "VersionAdd $P{Name} failed";
        sleep 1;    # distinct create times, the order is by id anyway
    }
    return $ID;
}

my %CI;
my $Domain = '.example.org';
$CI{app01} = NewCI(
    Class => 'Computer', Name => "$Prefix-app01$Domain",
    Versions => [
        { Depl => 'Planned',    Inci => 'Operational', Data => sub { return +{
            Vendor => C('Vendor A'), Description => C('first'),
            NIC => [ undef, { Content => 'eth0', IPAddress => C('192.0.2.10'),
                              IPoverDHCP => C($YesNoIDs->{No}) } ],
        } } },
        { Depl => 'Production', Inci => 'Operational', Data => sub { return +{
            Vendor => C('Vendor A'), Description => C('second'),
            CPU => [ undef, { Content => 'x86' }, { Content => 'arm' } ],
            NIC => [
                undef,
                { Content => 'eth0', IPAddress => C('192.0.2.10'), IPoverDHCP => C($YesNoIDs->{No}) },
                { Content => 'eth1', IPAddress => C('192.0.2.11'), IPoverDHCP => C($YesNoIDs->{Yes}) },
            ],
        } } },
        { Depl => 'Production', Inci => 'Incident', Name => "$Prefix-app01-renamed$Domain", Data => sub { return +{
            Vendor => C('Vendor B'), Description => C('third'), WarrantyExpirationDate => C('2030-01-31'),
            Type => C($TypeIDs->{$TypeName}),
        } } },
    ],
);
$CI{app02} = NewCI(
    Class => 'Computer', Name => "$Prefix-app02$Domain",
    Versions => [
        { Depl => 'Production', Inci => 'Operational', Data => sub { return +{
            Vendor => C('Vendor C'), FQDN => C("app02$Domain"),
        } } },
    ],
);
$CI{db01} = NewCI(
    Class => 'Computer', Name => "$Prefix-db01$Domain",
    Versions => [
        { Depl => 'Production', Inci => 'Operational', Data => sub { return +{ Vendor => C('Vendor D') } } },
        { Depl => 'Production', Inci => 'Operational', Data => sub { return +{ Vendor => C('Vendor D'), Model => C('Model 2') } } },
    ],
);
$CI{fw01} = NewCI(
    Class => '__CLASS__', Name => "$Prefix-fw01$Domain", Refs => { Parent => $CI{app01} },
    Versions => [
        { Depl => 'Production', Inci => 'Operational', Data => sub { my $R = shift; return +{
            Label => C('fw one'), Support => C($YesNoIDs->{Yes}), Parent => C( $R->{Parent} ),
            Installed => C('2026-02-03'), LastCheck => C('2026-03-04 05:06'),
            Port => [ undef, { Content => 'p1', Speed => C(1000) }, { Content => 'p2', Speed => C(10000) } ],
        } } },
        { Depl => 'Production', Inci => 'Operational', Data => sub { my $R = shift; return +{
            Label => C('fw one b'), Support => C($YesNoIDs->{No}), Parent => C( $R->{Parent} ),
            Installed => C('2026-02-03'), Port => [ undef, { Content => 'p1', Speed => C(1000) } ],
        } } },
    ],
);
$CI{fw02} = NewCI(
    Class => '__CLASS__', Name => "$Prefix-fw02$Domain", Refs => { Parent => $CI{app02} },
    Versions => [
        { Depl => 'Planned', Inci => 'Operational', Data => sub { my $R = shift; return +{
            Label => C('fw two'), Parent => C( $R->{Parent} ),
        } } },
    ],
);

# --- links: CI -> CI (two types) and ticket -> CI --------------------------------------------
my $TicketID = $TicketObject->TicketCreate(
    Title => "$Prefix ticket", Queue => 'Golden', Lock => 'unlock', Priority => '3 normal',
    State => 'new', CustomerID => 'golden-customer', CustomerUser => 'golden.customer',
    OwnerID => 1, UserID => 1,
) or die 'TicketCreate failed';
for my $Link (
    [ ITSMConfigItem => $CI{fw01}, ITSMConfigItem => $CI{app01}, 'DependsOn' ],
    [ ITSMConfigItem => $CI{fw02}, ITSMConfigItem => $CI{app02}, 'DependsOn' ],
    [ ITSMConfigItem => $CI{app02}, ITSMConfigItem => $CI{db01}, 'RelevantTo' ],
    [ Ticket => $TicketID, ITSMConfigItem => $CI{app01}, 'RelevantTo' ],
) {
    $LinkObject->LinkAdd(
        SourceObject => $Link->[0], SourceKey => $Link->[1],
        TargetObject => $Link->[2], TargetKey => $Link->[3],
        Type => $Link->[4], State => 'Valid', UserID => 1,
    ) or die "LinkAdd $Link->[4] failed";
}

# --- one binary attachment ---------------------------------------------------------------------
my $Binary = pack( 'C*', 0 .. 255 ) . "\xff\xfe golden attachment";
$CIObject->ConfigItemAttachmentAdd(
    ConfigItemID => $CI{app01}, Filename => 'golden-binary.bin',
    Content => $Binary, ContentType => 'application/octet-stream', UserID => 1,
) or die 'ConfigItemAttachmentAdd failed';

my %Dump = map { $_ => CIDump( $CI{$_} ) } sort keys %CI;
Emit( {
    class_id  => $ClassID + 0,
    ticket_id => $TicketID + 0,
    ticket_no => $TicketObject->TicketNumberLookup( TicketID => $TicketID ),
    cis       => \%Dump,
    attachment_b64 => MIME::Base64::encode_base64( $Binary, '' ),
} );
"""

_DELETE = r"""
my $Prefix = '__PREFIX__';
my $Ids = $CIObject->ConfigItemSearchExtended( Name => "$Prefix-*" ) || [];
my @Deleted;
for my $ID ( @{$Ids} ) {
    $CIObject->ConfigItemDelete( ConfigItemID => $ID, UserID => 1 ) and push @Deleted, $ID + 0;
}
my @Tickets = $TicketObject->TicketSearch(
    Title => "$Prefix ticket", Result => 'ARRAY', UserID => 1, ArchiveFlags => [ 'y', 'n' ],
);
$TicketObject->TicketDelete( TicketID => $_, UserID => 1 ) for @Tickets;
Emit( { cis => \@Deleted, tickets => scalar @Tickets } );
"""


def ensure_agent() -> None:
    """The agent of the GI cases: member of ``users`` and of ``itsm-configitem`` (``ro`` + ``rw``).

    Created once (a Znuny user cannot be deleted; the login stays in the throw-away peer), so
    ``Admin::User::Add`` answering "already exists" is fine.
    """
    try:
        znuny_console(
            "Admin::User::Add",
            "--user-name",
            AGENT_LOGIN,
            "--first-name",
            "Gold",
            "--last-name",
            "Imp",
            "--email-address",
            AGENT_EMAIL,
            "--password",
            AGENT_PASSWORD,
            "--group",
            "users",
        )
    except RuntimeError as exc:
        if "already" not in str(exc).lower() and "Can't add user" not in str(exc):
            raise
    for permission in ("ro", "rw"):
        znuny_console(
            "Admin::Group::UserLink",
            "--user-name",
            AGENT_LOGIN,
            "--group-name",
            "itsm-configitem",
            f"--permission={permission}",
        )


def znuny_json(body: str) -> Any:
    """Run Perl *body* in the peer (after :data:`PRELUDE`) and decode what it ``Emit``ted."""
    out = znuny_perl_eval(PRELUDE + body)
    match = re.search(r"GOLDEN_JSON_BEGIN\n(.*?)\nGOLDEN_JSON_END", out, re.S)
    assert match, f"no result block in peer output: {out[-2000:]!r}"
    return json.loads(match.group(1))


def create_scenario(prefix: str) -> dict[str, Any]:
    """Create the scenario data under *prefix*; returns the Perl-side dump (Znuny ids)."""
    script = (
        _SCENARIO.replace("__PREFIX__", prefix)
        .replace("__CLASS__", CLASS_NAME)
        .replace("__DEFINITION__", _DEFINITION)
    )
    data: dict[str, Any] = znuny_json(script)
    data["attachment"] = base64.b64decode(data.pop("attachment_b64"))
    return data


def delete_scenario(prefix: str) -> dict[str, Any]:
    """Delete every CI named ``<prefix>-*`` and the ticket of the scenario (idempotent)."""
    result: dict[str, Any] = znuny_json(_DELETE.replace("__PREFIX__", prefix))
    return result


def dump_cis(ids: list[int]) -> dict[int, dict[str, Any]]:
    """:func:`CIDump` of the given Znuny CI ids (state after later ``VersionAdd`` calls)."""
    body = "Emit({ map { $_ => CIDump($_) } (" + ",".join(str(int(i)) for i in ids) + ") });"
    raw: dict[str, Any] = znuny_json(body)
    return {int(k): v for k, v in raw.items()}


def add_version(ci_id: int, *, name: str, vendor: str) -> int:
    """One more Znuny version of a ``Computer`` CI (new name and vendor, states unchanged)."""
    body = f"""
my $CI = $CIObject->ConfigItemGet( ConfigItemID => {int(ci_id)} );
my $Last = $CIObject->VersionGet( ConfigItemID => {int(ci_id)}, XMLDataGet => 0 );
my $Def = $CIObject->DefinitionGet( ClassID => $CI->{{ClassID}} );
my $VersionID = $CIObject->VersionAdd(
    ConfigItemID => {int(ci_id)}, Name => '{name}', DefinitionID => $Def->{{DefinitionID}},
    DeplStateID => $Last->{{DeplStateID}}, InciStateID => $Last->{{InciStateID}},
    XMLData => [ undef, {{ Version => [ undef, {{ Vendor => [ undef, {{ Content => '{vendor}' }} ] }} ] }} ],
    UserID => 1,
) or die 'VersionAdd failed';
Emit({{ version_id => $VersionID + 0 }});
"""
    return int(znuny_json(body)["version_id"])


# --- Tiqora tables: snapshot / restore ----------------------------------------------------------


@dataclass
class TiqoraSnapshot:
    """The rows of :data:`SNAPSHOT_TABLES` (and the ``itsm.*`` settings) at one point in time.

    ``restore()`` puts the tables back exactly (foreign keys off for the copy), so the module
    leaves the shared golden database as it found it.
    """

    dsn: dict[str, Any]
    tables: dict[str, tuple[list[str], list[tuple[Any, ...]]]]
    settings: list[tuple[Any, ...]]

    @classmethod
    def take(cls, dsn: dict[str, Any]) -> TiqoraSnapshot:
        conn = pymysql.connect(**dsn)
        try:
            tables: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {}
            with conn.cursor() as cur:
                for table in SNAPSHOT_TABLES:
                    cur.execute(f"SELECT * FROM `{table}`")  # noqa: S608 - fixed table names
                    cols = [d[0] for d in cur.description]
                    tables[table] = (cols, list(cur.fetchall()))
                cur.execute("SELECT `key`, `value` FROM tiqora_settings WHERE `key` LIKE 'itsm.%%'")
                settings = list(cur.fetchall())
        finally:
            conn.close()
        return cls(dsn, tables, settings)

    def restore(self) -> None:
        conn = pymysql.connect(**self.dsn)
        try:
            with conn.cursor() as cur:
                cur.execute("SET FOREIGN_KEY_CHECKS=0")
                for table in SNAPSHOT_TABLES:
                    cur.execute(f"DELETE FROM `{table}`")  # noqa: S608
                for table, (cols, rows) in self.tables.items():
                    if not rows:
                        continue
                    names = ", ".join(f"`{c}`" for c in cols)
                    marks = ", ".join(["%s"] * len(cols))
                    cur.executemany(
                        f"INSERT INTO `{table}` ({names}) VALUES ({marks})",  # noqa: S608
                        rows,
                    )
                cur.execute("DELETE FROM tiqora_settings WHERE `key` LIKE 'itsm.%%'")
                if self.settings:
                    cur.executemany(
                        "INSERT INTO tiqora_settings (`key`, `value`) VALUES (%s, %s)",
                        self.settings,
                    )
                cur.execute("SET FOREIGN_KEY_CHECKS=1")
        finally:
            conn.close()


# --- the importer, called the way the job calls it ----------------------------------------------


async def run_importer(
    factory: async_sessionmaker[AsyncSession], *, dry_run: bool, page_size: int = 500
) -> ImportReport:
    """``run_import`` with its own run row (``dry_run`` or ``import``), no cancellation."""
    report, _run_id = await run_importer_with_id(factory, dry_run=dry_run, page_size=page_size)
    return report


async def run_importer_with_id(
    factory: async_sessionmaker[AsyncSession], *, dry_run: bool, page_size: int = 500
) -> tuple[ImportReport, int]:
    async with factory() as session:
        run = TiqoraItsmImportRun(
            kind="dry_run" if dry_run else "import",
            status="running",
            job_id=None,
            started_by=1,
            started_at=utc_now(),
            summary="{}",
        )
        session.add(run)
        await session.commit()
        run_id = int(run.id)

    async def progress(done: int, total: int | None, label: str) -> None:
        return None

    async def check_cancelled() -> None:
        return None

    report = await run_import(
        factory,
        run_id=run_id,
        user_id=1,
        dry_run=dry_run,
        page_size=page_size,
        progress=progress,
        check_cancelled=check_cancelled,
    )
    return report, run_id
