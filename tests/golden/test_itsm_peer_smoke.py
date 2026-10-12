"""Golden smoke test of the Znuny ITSM peers (I15, wave-4 itsm-core).

Runs against ``GOLDEN_PEER=znuny-6.5-itsm`` or ``znuny-7.3-itsm`` (ITSM tests) and against a
plain peer such as ``znuny-6.5`` (``test_plain_peer_has_no_itsm``): the toolkit runs one peer per
session, so each test is marked for the kind of peer it needs and skipped on the other.

    GOLDEN_PEER=znuny-6.5-itsm just golden-itsm-fetch znuny-6.5-itsm --record   # once
    GOLDEN_PEER=znuny-6.5-itsm just golden-up && just golden-seed
    GOLDEN=1 GOLDEN_PEER=znuny-6.5-itsm just golden-test   # -k itsm_peer_smoke
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pymysql
import pytest
import yaml
from _helpers import znuny_console, znuny_copy_in, znuny_perl_eval
from peer_env import resolve as resolve_peer

pytestmark = pytest.mark.golden

GOLDEN_DIR = Path(__file__).resolve().parent
SEED_SCRIPT = GOLDEN_DIR / "itsm_seed.pl"
SEED_REMOTE = "/tmp/itsm_seed.pl"  # noqa: S108 — path inside the throw-away peer container

ITSM_TABLES = (
    "configitem",
    "configitem_version",
    "configitem_definition",
    "general_catalog",
    "general_catalog_preferences",
    "cip_allocate",
)
_BLOCK = re.compile(r"^\| Name:\s+(?P<name>\S+)\n\| Version:\s+(?P<version>\S+)", re.M)


def _expected_packages(peer: str) -> tuple[list[str], list[dict[str, str]]]:
    base = resolve_peer(peer)["GOLDEN_BASE_PEER"]
    spec = yaml.safe_load((GOLDEN_DIR / "itsm-packages.yaml").read_text(encoding="utf-8"))[base]
    return list(spec["provides"]), list(spec["packages"])


def _installed_packages() -> dict[str, str]:
    """``{name: version}`` from the peer's ``Admin::Package::List``."""
    out = znuny_console("Admin::Package::List")
    return {m["name"]: m["version"] for m in _BLOCK.finditer(out)}


def _run_seed() -> dict[str, object]:
    znuny_copy_in(SEED_SCRIPT, SEED_REMOTE)
    out = znuny_perl_eval(f"do '{SEED_REMOTE}' or die $@ || $!;")
    match = re.search(r"GOLDEN_ITSM_SEED_BEGIN\n(.*?)\nGOLDEN_ITSM_SEED_END", out, re.S)
    assert match, f"seed produced no result block: {out!r}"
    result: dict[str, object] = json.loads(match.group(1))
    return result


@pytest.fixture(scope="module")
def itsm_seed(golden_peer_id: str) -> dict[str, object]:
    return _run_seed()


def _table_exists(conn: pymysql.Connection, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            (table,),
        )
        row = cur.fetchone()
    return bool(row and row[0])


@pytest.mark.itsm_peer
def test_itsm_packages_installed(golden_peer_id: str) -> None:
    provides, packages = _expected_packages(golden_peer_id)
    installed = _installed_packages()
    for name in provides:
        assert name in installed, f"{name} not installed; installed: {sorted(installed)}"
    # Individually listed packages (7.x) must be at the recorded version; the 6.5 bundle
    # carries its own sub-package versions, only the bundle file is pinned (sha256).
    for package in packages:
        if package["name"] in provides:
            assert installed[package["name"]] == package["version"], package["name"]
    for package in packages:
        assert package["sha256"], f"{package['name']}: sha256 not recorded"


@pytest.mark.itsm_peer
def test_itsm_tables_exist(golden_conn: pymysql.Connection) -> None:
    missing = [t for t in ITSM_TABLES if not _table_exists(golden_conn, t)]
    assert not missing, f"missing ITSM tables: {missing}"


@pytest.mark.itsm_peer
def test_seed_created_cis_and_links(itsm_seed: dict[str, object]) -> None:
    cis = itsm_seed["cis"]
    assert isinstance(cis, dict)
    numbers = sorted(ci["number"] for ci in cis.values())
    assert len(numbers) == 3
    ticket_id = itsm_seed["ticket_id"]
    web = cis["web01"]
    out = znuny_perl_eval(
        r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden'});
my $CI   = $Kernel::OM->Get('Kernel::System::ITSMConfigItem');
my $Link = $Kernel::OM->Get('Kernel::System::LinkObject');
my $JSON = $Kernel::OM->Get('Kernel::System::JSON');
my %Found;
for my $Number ("""
        + ",".join(f"'{n}'" for n in numbers)
        + r""") {
    my $IDs = $CI->ConfigItemSearchExtended(Number => $Number);
    $Found{$Number} = scalar @{$IDs || []};
}
my $List = $Link->LinkList(Object => 'Ticket', Key => """
        + str(ticket_id)
        + r""", State => 'Valid', UserID => 1) || {};
my $Versions = $CI->VersionList(ConfigItemID => """
        + str(web["id"])
        + r""") || [];
print "GOLDEN_ITSM_RESULT\n" . $JSON->Encode(Data => {
    found => \%Found,
    ticket_link_objects => [ sort keys %{$List} ],
    relevant_to_cis => [ sort map { keys %{$_} }
        values %{$List->{ITSMConfigItem}{RelevantTo} || {}} ],
    web_versions => scalar @{$Versions},
});
"""
    )
    data = json.loads(out.split("GOLDEN_ITSM_RESULT\n", 1)[1])
    assert data["found"] == dict.fromkeys(numbers, 1)
    assert "ITSMConfigItem" in data["ticket_link_objects"]
    assert [str(web["id"])] == data["relevant_to_cis"]
    assert data["web_versions"] == 2


@pytest.mark.itsm_peer
def test_seed_is_idempotent(itsm_seed: dict[str, object]) -> None:
    again = _run_seed()
    assert again["created"] == 0
    assert again["service_id"] == itsm_seed["service_id"]


@pytest.mark.plain_peer
def test_plain_peer_has_no_itsm(golden_conn: pymysql.Connection) -> None:
    """The base peer volume stays ITSM-free (separate compose project / volume)."""
    present = [t for t in ITSM_TABLES if _table_exists(golden_conn, t)]
    assert not present, f"plain peer has ITSM tables: {present}"
    installed = _installed_packages()
    assert not {"GeneralCatalog", "ITSMCore", "ITSMConfigurationManagement"} & set(installed)
