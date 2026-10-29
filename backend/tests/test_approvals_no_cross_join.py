"""Static guard: nothing under ``tiqora/approvals`` joins ``tiqora_*`` with a Znuny table.

Prod MariaDB mixes ``general_ci`` and ``unicode_ci`` collations (error 1267), so keys are
loaded from one side and the other is queried with an ``IN`` list. The checker is the one the
ITSM package uses (``tests/test_itsm_no_cross_join.py``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tests.test_itsm_no_cross_join import find_cross_joins, znuny_tables

APPROVALS_SRC = Path(__file__).resolve().parents[1] / "src" / "tiqora" / "approvals"


def test_no_cross_join_in_approvals_package() -> None:
    import tiqora.approvals.models  # noqa: F401  (registers the ORM classes for the class->table map)

    assert APPROVALS_SRC.is_dir()
    assert find_cross_joins(APPROVALS_SRC, znuny_tables()) == []


def test_no_cross_join_in_approvals_routers(tmp_path: Path) -> None:
    """The REST routers (agent, admin, portal) and their shared helper query on their own."""
    import tiqora.approvals.models  # noqa: F401

    api = APPROVALS_SRC.parent / "api"
    files = [
        api / "v1" / "approvals.py",
        api / "v1" / "_approvals_common.py",
        api / "v1" / "admin" / "approvals.py",
        api / "portal" / "approvals.py",
    ]
    for file in files:
        assert file.is_file(), file
        shutil.copy(file, tmp_path / f"{file.parent.name}_{file.name}")
    assert len(list(tmp_path.glob("*.py"))) == len(files)
    assert find_cross_joins(tmp_path, znuny_tables()) == []
