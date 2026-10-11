"""Shared fixtures for the GenericInterface requester tests (``tiqora.gi``).

* :data:`GI_MARK_TABLES` - every table GI tests may add autoincrement rows to, children
  first; pass it to ``tests._row_cleanup.snapshot_marks`` / ``delete_above_marks``.
* :func:`insert_webservice` / :func:`cleanup_webservices` - seed and remove
  ``gi_webservice_config`` rows (plus their Tiqora-side ``gi.webservice.<id>.*`` settings).
* :func:`requester_config` - a minimal valid Znuny webservice config with a requester.

Only made-up hosts (``example.org``) appear here.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import yaml
from sqlalchemy import bindparam, delete, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DatabaseError

from tiqora.db.tiqora.models import TiqoraSettings

# Children first: the order is the delete order.
GI_MARK_TABLES: tuple[str, ...] = (
    "tiqora_gi_debug_content",
    "tiqora_gi_debug_entry",
    "tiqora_job",
    "tiqora_event_outbox",
    "tiqora_cache_invalidation",
    "ticket_history",
    "dynamic_field_value",
    "article_data_mime_attachment",
    "article_data_mime_plain",
    "article_data_mime",
    "article",
    "ticket",
    "ticket_number_counter",
)

_SEED_TIME = datetime(2026, 1, 1, 10, 0, 0)


def insert_webservice(
    conn: Connection, *, ws_id: int, name: str, config: dict[str, Any], valid: bool = True
) -> None:
    """Insert one ``gi_webservice_config`` row (YAML like Znuny stores it)."""
    dumped = yaml.safe_dump(config, sort_keys=True)
    # MariaDB: LONGBLOB (bytes); PostgreSQL: TEXT (a bytes bind would be stored as hex).
    stored: bytes | str = dumped if conn.dialect.name == "postgresql" else dumped.encode()
    conn.execute(
        text(
            "INSERT INTO gi_webservice_config (id, name, config, valid_id, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (:id, :name, :config, :valid_id, :t, 1, :t, 1)"
        ),
        {
            "id": ws_id,
            "name": name,
            "config": stored,
            "valid_id": 1 if valid else 2,
            "t": _SEED_TIME,
        },
    )


def requester_config(
    *,
    host: str = "https://crm.example.org",
    invokers: dict[str, Any],
    transport: str = "HTTP::REST",
    error_handling: dict[str, Any] | None = None,
    debug_threshold: str = "debug",
    transport_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Minimal valid Znuny webservice config with a requester and no provider.

    Every invoker gets the controller ``/Ticket/:TicketID``; ``transport_extra`` is merged
    into the transport ``Config`` last (it may override the defaults).
    ``ErrorHandlingPriority`` lists the ``error_handling`` modules in their given order.
    """
    transport_config: dict[str, Any] = {
        "Host": host,
        "DefaultCommand": "POST",
        "Timeout": "30",
        "InvokerControllerMapping": {
            name: {"Controller": "/Ticket/:TicketID"} for name in invokers
        },
    }
    if transport_extra:
        transport_config.update(copy.deepcopy(transport_extra))
    modules = copy.deepcopy(error_handling) if error_handling else {}
    return {
        "Debugger": {"DebugThreshold": debug_threshold, "TestMode": "0"},
        "Provider": {"Transport": {"Type": ""}},
        "Requester": {
            "Transport": {"Type": transport, "Config": transport_config},
            "Invoker": copy.deepcopy(invokers),
            "ErrorHandling": modules,
            "ErrorHandlingPriority": list(modules),
        },
    }


def cleanup_webservices(conn: Connection, ws_ids: Iterable[int]) -> None:
    """Delete the ``gi_webservice_config`` rows and their ``gi.webservice.<id>.*`` settings."""
    ids = list(ws_ids)
    if not ids:
        return
    for ws_id in ids:
        # Via the model: ``key`` is reserved in MySQL and needs dialect-specific quoting.
        # A savepoint keeps a missing tiqora_settings table from aborting the transaction.
        try:
            with conn.begin_nested():
                conn.execute(
                    delete(TiqoraSettings).where(
                        TiqoraSettings.key.like(f"gi.webservice.{ws_id}.%")
                    )
                )
        except DatabaseError:
            pass
    conn.execute(
        text("DELETE FROM gi_webservice_config WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": ids},
    )
