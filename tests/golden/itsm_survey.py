#!/usr/bin/env python3
"""Survey of the Znuny ITSM packages on a golden ITSM peer (wave-4 itsm-core, Task 2).

    python tests/golden/itsm_survey.py znuny-6.5-itsm \\
        --out backend/tests/fixtures/znuny-itsm/itsm-6/
    python tests/golden/itsm_survey.py znuny-7.3-itsm \\
        --out backend/tests/fixtures/znuny-itsm/itsm-7/ --verify

Needs a *fresh* peer (``GOLDEN_PEER=<peer> just golden-up``, packages installed, ``golden-seed`` /
``itsm_seed.pl`` NOT run). Reads the installed packages from the peer's ``package_repository`` and
generates, never by hand:

* ``schema.<dialect>.sql`` / ``schema-post.<dialect>.sql``: the packages' ``TableCreate`` /
  ``TableAlter`` elements run through Znuny's ``Dev::Tools::Database::XML2SQL`` for ``mysql`` and
  ``postgresql`` (``--split-files``: foreign keys / PostgreSQL indexes go to the post file);
* ``initial_insert.sql``: the rows the packages leave behind (package-owned tables completely, plus
  the rows they add to a few core tables), dialect-neutral ``INSERT`` statements with explicit
  column lists; install time stamps are normalised to ``2000-01-01 00:00:00``;
* ``sysconfig.json``: ``{name: effective_value}`` of the ITSM related ``sysconfig_default`` rows;
* ``packages.json``: package names, versions and the installation order;
* ``tables.md``: the column/index listing of every package table (input for docs/itsm-import.md).

``--behaviour`` (Task 3) does not generate fixtures: it copies ``itsm_behaviour.pl`` into a *fresh*
peer, runs the scenarios (CIP matrix, service incident state, link side effects, permissions) and
merges the result into ``<out>/<profile>.json`` under the key ``"peer"``. The pinned answers
(``"questions"``) stay hand-maintained; ``test_itsm_behaviour_vectors.py`` compares both.

    python tests/golden/itsm_survey.py znuny-6.5-itsm --behaviour \\
        --out backend/tests/fixtures/znuny-itsm/behaviour/

``--verify`` loads the generated DDL once into a throw-away MariaDB and PostgreSQL container, on
top of the peer's own core schema, and compares the resulting table list with the peer.
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

GOLDEN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(GOLDEN_DIR))

import _helpers  # noqa: E402
from peer_env import resolve as resolve_peer  # noqa: E402

DIALECTS = ("mysql", "postgresql")
NORMALISED_TIME = "2000-01-01 00:00:00"
REMOTE_DIR = "/tmp/itsm_survey"  # noqa: S108 - inside the throw-away peer container
BEHAVIOUR_SCRIPT = GOLDEN_DIR / "itsm_behaviour.pl"
BEHAVIOUR_REMOTE = "/tmp/itsm_behaviour.pl"  # noqa: S108 - inside the throw-away peer container
# peer id -> fixture profile (backend/tests/fixtures/znuny-itsm/<profile>/)
PEER_PROFILES = {"znuny-6.5-itsm": "itsm-6", "znuny-7.3-itsm": "itsm-7"}

# Core tables the packages add rows to (create_time >= first package install).
CORE_ROW_TABLES = (
    "permission_groups",
    "group_user",
    "ticket_type",
    "ticket_state",
    "ticket_history_type",
    "dynamic_field",
)
# Tables that gain rows during a package install but are not ITSM data.
IGNORED_NEW_ROW_TABLES = frozenset(
    {
        "package_repository",
        "sysconfig_default",
        "sysconfig_default_version",
        "sysconfig_modified",
        "sysconfig_modified_version",
        "sysconfig_deployment",
    }
)
# Tables a seed / use of the peer would fill; the survey must run before.
MUST_BE_EMPTY = ("configitem", "configitem_version", "service", "sla")

SYSCONFIG_PREFIXES = (
    "ITSM",
    "LinkObject::",
    "Ticket::Frontend::AgentTicketAddtlITSMField",
    "Ticket::Frontend::AgentTicketDecision",
    "Ticket::Frontend::AgentITSM",
    "Frontend::Module###AgentITSM",
)


def log(message: str) -> None:
    print(f"[itsm-survey] {message}", file=sys.stderr)


# --- peer access -------------------------------------------------------------------------------


def peer_env(peer: str) -> dict[str, str]:
    env = resolve_peer(peer)
    for key, value in env.items():
        os.environ[key] = str(value)
    os.environ.setdefault("GOLDEN_COMPOSE_PROJECT", env["GOLDEN_COMPOSE_PROJECT"])
    return {k: str(v) for k, v in env.items()}


def db_connect(env: dict[str, str]) -> Any:
    import pymysql

    url = urlparse(env["GOLDEN_DB_URL"].replace("mysql+pymysql", "mysql"))
    return pymysql.connect(
        host=url.hostname,
        port=url.port or 3306,
        user=url.username,
        password=url.password or "",
        database=url.path.lstrip("/"),
        charset="utf8mb4",
    )


def peer_exec(*cmd: str, stdin: str | None = None) -> str:
    """Run a command in the peer container as the Znuny user; returns stdout."""
    result = subprocess.run(  # noqa: S603
        [*_helpers._compose_base(), "exec", "-T", "-u", "otrs", "znuny", *cmd],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"peer command failed: {' '.join(cmd)}\n{result.stderr}\n{result.stdout}")
    return result.stdout


def xml2sql(source_remote: str, dialect: str, name: str) -> tuple[str, str]:
    """Run XML2SQL on a file inside the peer; returns (main_sql, post_sql)."""
    peer_exec(
        "perl",
        "/opt/otrs/bin/otrs.Console.pl",
        "Dev::Tools::Database::XML2SQL",
        "--database-type",
        dialect,
        "--source-path",
        source_remote,
        "--target-directory",
        REMOTE_DIR,
        "--target-filename",
        name,
        "--split-files",
        "--quiet",
        "--no-ansi",
    )
    main = peer_exec("cat", f"{REMOTE_DIR}/{name}.{dialect}.sql")
    post = peer_exec("cat", f"{REMOTE_DIR}/{name}-post.{dialect}.sql")
    return main, post


def copy_in(content: bytes, remote: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / Path(remote).name
        local.write_bytes(content)
        _helpers.znuny_copy_in(local, remote)


# --- packages ----------------------------------------------------------------------------------


def installed_packages(cur: Any) -> list[dict[str, Any]]:
    cur.execute(
        "select id, name, version, content, create_time from package_repository order by id"
    )
    rows = []
    for pid, name, version, content, created in cur.fetchall():
        root = ET.fromstring(content)  # noqa: S314 - the peer's own, hash-pinned packages
        rows.append(
            {
                "id": pid,
                "name": name,
                "version": version,
                "framework": [f.text for f in root.findall("Framework")],
                "root": root,
                "installed_at": created,
            }
        )
    return rows


def database_xml(root: ET.Element) -> tuple[bytes | None, list[str]]:
    """TableCreate/TableAlter of a package's DatabaseInstall (Insert rows come from the dump)."""
    db = ET.Element("database", Name="otrs")
    tables: list[str] = []
    for install in root.findall("DatabaseInstall"):
        for element in install:
            if element.tag == "Insert":
                continue
            db.append(element)
            if element.tag == "TableCreate":
                tables.append(element.get("Name", ""))
    if not len(db):
        return None, tables
    return ET.tostring(db, encoding="utf-8", xml_declaration=True), tables


# --- DDL ---------------------------------------------------------------------------------------


def comment(dialect: str, text: str) -> str:
    return f"{'#' if dialect == 'mysql' else '--'} {text}\n"


def generate_ddl(packages: list[dict[str, Any]], out: Path) -> list[str]:
    """Write schema*.sql per dialect; return the created table names (install order)."""
    peer_exec("mkdir", "-p", REMOTE_DIR)
    created: list[str] = []
    mains = {d: "" for d in DIALECTS}
    posts = {d: "" for d in DIALECTS}
    for package in packages:
        xml_bytes, tables = database_xml(package["root"])
        if xml_bytes is None:
            continue
        created += tables
        name = package["name"]
        remote = f"{REMOTE_DIR}/{name}.xml"
        copy_in(xml_bytes, remote)
        for dialect in DIALECTS:
            main, post = xml2sql(remote, dialect, name)
            tag = f"package {name} {package['version']}"
            mains[dialect] += comment(dialect, tag) + main.rstrip() + "\n"
            posts[dialect] += comment(dialect, tag) + post.rstrip() + "\n"
    for dialect in DIALECTS:
        head = comment(dialect, "generated by tests/golden/itsm_survey.py (Znuny XML2SQL)")
        (out / f"schema.{dialect}.sql").write_text(head + mains[dialect], encoding="utf-8")
        (out / f"schema-post.{dialect}.sql").write_text(head + posts[dialect], encoding="utf-8")
    return created


# --- rows --------------------------------------------------------------------------------------


def literal(value: Any, table: str, column: str) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes | bytearray):
        value = bytes(value).decode("utf-8")
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, dt.datetime):
        return f"'{NORMALISED_TIME}'"
    if isinstance(value, dt.date):
        return f"'{value.isoformat()}'"
    text = str(value)
    if "\\" in text:
        # MySQL treats a backslash in a literal as an escape, PostgreSQL does not.
        raise SystemExit(f"{table}.{column}: backslash in a value; extend literal() first")
    if "\x00" in text:
        raise SystemExit(f"{table}.{column}: NUL byte in a value")
    return "'" + text.replace("'", "''") + "'"


def table_columns(cur: Any, table: str) -> list[str]:
    cur.execute(
        "select column_name from information_schema.columns "
        "where table_schema = database() and table_name = %s order by ordinal_position",
        (table,),
    )
    return [row[0] for row in cur.fetchall()]


def dump_rows(cur: Any, table: str, where: str = "", args: tuple[Any, ...] = ()) -> tuple[str, int]:
    columns = table_columns(cur, table)
    order = ", ".join(str(i + 1) for i in range(min(3, len(columns))))
    cur.execute(f"select * from `{table}` {where} order by {order}", args)  # noqa: S608
    rows = cur.fetchall()
    out = []
    for row in rows:
        values = ", ".join(literal(v, table, c) for v, c in zip(row, columns, strict=True))
        out.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values});\n")
    return "".join(out), len(rows)


def dump_initial_rows(cur: Any, package_tables: list[str], install_start: dt.datetime) -> str:
    """INSERTs for package tables (all rows) and the rows packages add to core tables."""
    cur.execute(
        "select table_name from information_schema.columns "
        "where table_schema = database() and column_name = 'create_time'"
    )
    unexpected = []
    for (table,) in cur.fetchall():
        if table in package_tables or table in CORE_ROW_TABLES or table in IGNORED_NEW_ROW_TABLES:
            continue
        cur.execute(f"select count(*) from `{table}` where create_time >= %s", (install_start,))  # noqa: S608
        if cur.fetchone()[0]:
            unexpected.append(table)
    if unexpected:
        raise SystemExit(
            f"packages added rows to tables the survey does not know: {unexpected}; "
            "add them to CORE_ROW_TABLES or IGNORED_NEW_ROW_TABLES"
        )
    parts = [
        "-- generated by tests/golden/itsm_survey.py; load after the Znuny core initial_insert\n"
    ]
    for table in package_tables:
        sql, count = dump_rows(cur, table)
        log(f"rows {table}: {count}")
        if count:
            parts.append(f"-- {table}\n{sql}")
    for table in CORE_ROW_TABLES:
        sql, count = dump_rows(cur, table, "where create_time >= %s", (install_start,))
        log(f"rows {table} (added by packages): {count}")
        if count:
            parts.append(f"-- {table}\n{sql}")
    return "".join(parts)


# --- sysconfig / packages / tables.md -----------------------------------------------------------


def dump_sysconfig(cur: Any) -> dict[str, Any]:
    clause = " or ".join(["name like %s"] * len(SYSCONFIG_PREFIXES))
    cur.execute(
        f"select name, effective_value from sysconfig_default where {clause} order by name",  # noqa: S608
        tuple(p.replace("_", r"\_") + "%" for p in SYSCONFIG_PREFIXES),
    )
    result: dict[str, Any] = {}
    for name, value in cur.fetchall():
        text = bytes(value).decode("utf-8") if isinstance(value, bytes | bytearray) else value
        result[name] = yaml.safe_load(text) if text else text  # effective_value is YAML text
    return result


def tables_markdown(cur: Any, tables: list[str], altered: dict[str, list[str]]) -> str:
    out = ["<!-- generated by tests/golden/itsm_survey.py -->\n"]
    for table in tables:
        cur.execute(
            "select column_name, column_type, is_nullable, column_key, extra "
            "from information_schema.columns where table_schema = database() and table_name = %s "
            "order by ordinal_position",
            (table,),
        )
        title = (
            f"`{table}` (nur die von ITSM hinzugefügten Spalten)"
            if table in altered
            else f"`{table}`"
        )
        out.append(f"\n#### {title}\n\n| Spalte | Typ | NULL | Schlüssel |\n|---|---|---|---|\n")
        for column, ctype, nullable, key, extra in cur.fetchall():
            if table in altered and column not in altered[table]:
                continue
            flag = f"{key} {extra}".strip()
            out.append(f"| `{column}` | `{ctype}` | {nullable} | {flag} |\n")
        cur.execute(
            "select index_name, non_unique, group_concat(column_name order by seq_in_index) "
            "from information_schema.statistics "
            "where table_schema = database() and table_name = %s "
            "group by index_name, non_unique order by index_name",
            (table,),
        )
        indexes = cur.fetchall()
        if indexes:
            out.append(
                "\nIndizes: "
                + "; ".join(
                    f"`{name}` ({'unique ' if not nonunique else ''}{cols})"
                    for name, nonunique, cols in indexes
                )
                + "\n"
            )
    return "".join(out)


def altered_columns(packages: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for package in packages:
        for install in package["root"].findall("DatabaseInstall"):
            for alter in install.findall("TableAlter"):
                for add in alter.findall("ColumnAdd"):
                    result.setdefault(alter.get("Name", ""), []).append(add.get("Name", ""))
    return result


# --- verify ------------------------------------------------------------------------------------


def _run(
    cmd: list[str], stdin: str | None = None, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, input=stdin, capture_output=True, text=True, timeout=timeout, check=False
    )  # noqa: S603


def core_ddl() -> dict[str, dict[str, str]]:
    """The peer's own core schema/initial rows per dialect (6.5 and 7.x differ)."""
    result: dict[str, dict[str, str]] = {d: {} for d in DIALECTS}
    for dialect in DIALECTS:
        for base in ("schema", "initial_insert", "schema-post"):
            result[dialect][base] = peer_exec(
                "cat", f"/opt/otrs/scripts/database/{base}.{dialect}.sql"
            )
    return result


def wait_for(cmd: list[str], what: str) -> None:
    for _ in range(90):
        if _run(cmd).returncode == 0:
            return
        time.sleep(1)
    raise SystemExit(f"{what} did not become ready")


# Per dialect: docker run arguments, client command prefix (inside the container) for readiness,
# loading a script from stdin and listing the tables.
_VERIFY_DB = {
    "mysql": {
        "image": ["-e", "MARIADB_ROOT_PASSWORD=x", "-e", "MARIADB_DATABASE=z", "mariadb:10.11"],
        "ready": ["mariadb", "-uroot", "-px", "-e", "select 1"],
        "load": ["mariadb", "-uroot", "-px", "z"],
        "names": [
            "mariadb", "-uroot", "-px", "z", "-N", "-e",
            "select table_name from information_schema.tables where table_schema='z'",
        ],
    },
    "postgresql": {
        "image": ["-e", "POSTGRES_PASSWORD=x", "-e", "POSTGRES_DB=z", "postgres:16"],
        "ready": ["pg_isready", "-U", "postgres", "-d", "z"],
        "load": ["psql", "-U", "postgres", "-d", "z", "-v", "ON_ERROR_STOP=1", "-q"],
        "names": [
            "psql", "-U", "postgres", "-d", "z", "-At", "-c",
            "select tablename from pg_tables where schemaname='public'",
        ],
    },
}  # fmt: skip


def verify(out: Path, peer_tables: list[str]) -> None:
    core = core_ddl()
    containers: list[str] = []
    try:
        for dialect in DIALECTS:
            spec = _VERIFY_DB[dialect]
            run = _run(["docker", "run", "-d", "--rm", *spec["image"]])
            if run.returncode != 0:
                raise SystemExit(run.stderr)
            container = run.stdout.strip()
            containers.append(container)
            ready = ["docker", "exec", container, *spec["ready"]]
            wait_for(ready, f"{dialect} container")
            time.sleep(3)  # postgres restarts once after initdb
            wait_for(ready, f"{dialect} container")
            load = ["docker", "exec", "-i", container, *spec["load"]]
            steps = [
                ("core schema", core[dialect]["schema"]),
                ("core initial_insert", core[dialect]["initial_insert"]),
                ("core schema-post", core[dialect]["schema-post"]),
                ("itsm schema", (out / f"schema.{dialect}.sql").read_text("utf-8")),
                ("itsm initial_insert", (out / "initial_insert.sql").read_text("utf-8")),
                ("itsm schema-post", (out / f"schema-post.{dialect}.sql").read_text("utf-8")),
            ]
            for label, sql in steps:
                result = _run(load, stdin=sql)
                if result.returncode != 0:
                    raise SystemExit(f"[{dialect}] {label} failed:\n{result.stderr[-2000:]}")
                log(f"[{dialect}] {label}: ok")
            names = _run(["docker", "exec", container, *spec["names"]])
            missing = sorted(set(peer_tables) - set(names.stdout.split()))
            if missing:
                raise SystemExit(f"[{dialect}] tables missing after load: {missing}")
            log(f"[{dialect}] all {len(peer_tables)} package tables present")
    finally:
        for container in containers:
            _run(["docker", "rm", "-f", container])


# --- main --------------------------------------------------------------------------------------


# --- behaviour vectors (Task 3) ------------------------------------------------------------------


def capture_behaviour(peer: str, env: dict[str, str], out: Path) -> Path:
    """Run ``itsm_behaviour.pl`` in the (fresh) peer and merge the result into ``<profile>.json``."""
    profile = PEER_PROFILES.get(peer)
    if profile is None:
        raise SystemExit(f"no behaviour profile for peer {peer}")
    _helpers.znuny_copy_in(BEHAVIOUR_SCRIPT, BEHAVIOUR_REMOTE)
    raw = _helpers.znuny_perl_eval(f"do '{BEHAVIOUR_REMOTE}' or die $@ || $!;")
    match = re.search(r"GOLDEN_ITSM_BEHAVIOUR_BEGIN\n(.*?)\nGOLDEN_ITSM_BEHAVIOUR_END", raw, re.S)
    if not match:
        raise SystemExit(f"behaviour script produced no result block: {raw!r}")
    results = json.loads(match.group(1))
    if results.get("already_run"):
        raise SystemExit("the behaviour scenarios already ran on this peer: use a fresh one")
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{profile}.json"
    document = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    document["profile"] = profile
    document["peer"] = {
        "peer": peer,
        "znuny_version": env.get("GOLDEN_VERSION", ""),
        "captured_with": "tests/golden/itsm_survey.py --behaviour (tests/golden/itsm_behaviour.pl)",
        "results": results,
    }
    target.write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log(f"behaviour results -> {target}")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("peer")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--verify", action="store_true", help="load the DDL into throw-away DBs")
    parser.add_argument(
        "--behaviour",
        action="store_true",
        help="run itsm_behaviour.pl on a fresh peer and merge the results into <out>/<profile>.json",
    )
    args = parser.parse_args(argv)

    env = peer_env(args.peer)
    if env.get("GOLDEN_ITSM") != "1":
        raise SystemExit(f"{args.peer} is not an ITSM peer")
    if args.behaviour:
        capture_behaviour(args.peer, env, args.out)
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    conn = db_connect(env)
    cur = conn.cursor()
    for table in MUST_BE_EMPTY:
        cur.execute(f"select count(*) from `{table}`")  # noqa: S608
        if cur.fetchone()[0]:
            raise SystemExit(
                f"{table} is not empty: run the survey on a fresh peer (before itsm_seed.pl)"
            )

    packages = installed_packages(cur)
    install_start = min(p["installed_at"] for p in packages)
    log("packages: " + ", ".join(f"{p['name']} {p['version']}" for p in packages))

    created = generate_ddl(packages, args.out)
    (args.out / "initial_insert.sql").write_text(
        dump_initial_rows(cur, created, install_start), encoding="utf-8"
    )
    sysconfig = dump_sysconfig(cur)
    (args.out / "sysconfig.json").write_text(
        json.dumps(sysconfig, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    altered = altered_columns(packages)
    (args.out / "packages.json").write_text(
        json.dumps(
            {
                "peer": args.peer,
                "znuny_version": env["GOLDEN_VERSION"],
                "packages": [
                    {"name": p["name"], "version": p["version"], "framework": p["framework"]}
                    for p in packages
                ],
                "tables": created,
                "altered_columns": altered,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    (args.out / "tables.md").write_text(
        tables_markdown(cur, created + sorted(altered), altered), encoding="utf-8"
    )
    log(f"{len(created)} tables, {len(sysconfig)} sysconfig entries -> {args.out}")
    if args.verify:
        verify(args.out, created)
    return 0


if __name__ == "__main__":
    sys.exit(main())
