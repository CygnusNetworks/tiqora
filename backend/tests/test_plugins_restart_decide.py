"""Pure restart decisions (no database)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tiqora.plugins import restart, state
from tiqora.plugins.restart import Decision, ProcessRow, RestartRow, decide, restart_roles

T0 = datetime(2026, 10, 12, 8, 0, 0)


def P(i: str, role: str, gen: int, hb: int = 0, exiting: int | None = None) -> ProcessRow:
    return ProcessRow(
        i,
        role,
        gen,
        T0 + timedelta(seconds=hb),
        None if exiting is None else T0 + timedelta(seconds=exiting),
    )


R = RestartRow(
    generation=5,
    requested_at=T0,
    roles=("worker", "mcp", "api"),
    finished_at=None,
    failed_message=None,
)


def D(
    me: ProcessRow,
    procs: list[ProcessRow],
    at: int = 0,
    *,
    target: RestartRow | None = R,
    enabled: bool = True,
    busy: bool = False,
) -> Decision:
    return decide(
        me,
        target,
        procs,
        now=T0 + timedelta(seconds=at),
        timeout_seconds=180,
        enabled=enabled,
        busy=busy,
    )


def test_decide_disabled_stays() -> None:
    p = P("w1", "worker", 4)
    assert D(p, [p], enabled=False).action == "stay"


def test_decide_no_target_or_finished_or_failed_stays() -> None:
    p = P("w1", "worker", 4)
    assert D(p, [p], target=None).action == "stay"
    done = RestartRow(5, T0, ("worker",), T0, None)
    failed = RestartRow(5, T0, ("worker",), None, "x")
    assert D(p, [p], target=done).action == "stay"
    assert D(p, [p], target=failed).action == "stay"


def test_decide_worker_goes_first() -> None:
    procs = [P("w1", "worker", 4), P("m1", "mcp", 4), P("a1", "api", 4)]
    assert D(procs[0], procs).action == "exit"
    assert D(procs[1], procs).action == "stay"
    assert D(procs[2], procs).action == "stay"


def test_decide_mcp_after_worker_back() -> None:
    procs = [P("w2", "worker", 5, hb=20), P("m1", "mcp", 4, hb=20), P("a1", "api", 4, hb=20)]
    assert D(procs[1], procs, at=25).action == "exit"
    assert D(procs[2], procs, at=25).action == "stay"


def test_decide_two_api_one_at_a_time() -> None:
    base = [P("w2", "worker", 5, hb=40), P("m2", "mcp", 5, hb=40)]
    a1, a2 = P("a1", "api", 4, hb=40), P("a2", "api", 4, hb=40)
    # a1 and a2 are old: the smaller id leaves first
    assert D(a1, [*base, a1, a2], at=40).action == "exit"
    assert D(a2, [*base, a1, a2], at=40).action == "stay"
    # a1 left, nothing new yet: a2 keeps waiting
    a1x = P("a1", "api", 4, hb=40, exiting=41)
    assert D(a2, [*base, a1x, a2], at=45).action == "stay"
    # a new api (generation 5) is live: now a2 may leave
    a3 = P("a3", "api", 5, hb=70)
    a2b = P("a2", "api", 4, hb=70)
    assert D(a2b, [*base, a1x, a2b, a3], at=70).action == "exit"


def test_decide_old_heartbeat_counts_as_gone() -> None:
    w_old = P("w1", "worker", 4, hb=-60)  # heartbeat 60 s ago
    w_new = P("w2", "worker", 5, hb=0)
    m = P("m1", "mcp", 4)
    assert D(m, [w_old, w_new, m]).action == "exit"
    # while the old worker still beats, mcp has to wait
    assert D(m, [P("w1", "worker", 4), w_new, m]).action == "stay"


def test_timeout_stops_sequence() -> None:
    w_gone = P("w1", "worker", 4, hb=5, exiting=5)
    m = P("m1", "mcp", 4, hb=200)
    d = D(m, [w_gone, m], at=200)
    assert d.action == "fail"
    assert d.message is not None and "Hintergrunddienst" in d.message
    # before the timeout it only waits
    assert (
        D(P("m1", "mcp", 4, hb=100), [w_gone, P("m1", "mcp", 4, hb=100)], at=100).action == "stay"
    )


def test_busy_worker_is_not_a_timeout() -> None:
    # an old worker that has not left (a plugin job runs there) never makes mcp fail
    w = P("w1", "worker", 4, hb=500)
    m = P("m1", "mcp", 4, hb=500)
    assert D(m, [w, m], at=500).action == "stay"


def test_busy_process_stays() -> None:
    w = P("w1", "worker", 4)
    assert D(w, [w], busy=True).action == "stay"
    assert D(w, [w], busy=False).action == "exit"


def test_finish_when_all_done() -> None:
    procs = [
        P("w2", "worker", 5, hb=60),
        P("m2", "mcp", 5, hb=60),
        P("a2", "api", 5, hb=60),
        P("a1", "api", 4, hb=10, exiting=11),
    ]
    assert D(procs[2], procs, at=60).action == "finish"
    # the old api still beats: not done
    old = P("a1", "api", 4, hb=60)
    assert D(procs[2], [*procs[:3], old], at=60).action == "stay"
    # a restarted non-api process never finishes the sequence
    assert D(procs[0], procs, at=60).action == "stay"


def test_finish_candidate_fails_when_a_role_timed_out() -> None:
    a = P("a2", "api", 5, hb=400)
    w_gone = P("w1", "worker", 4, hb=5, exiting=5)
    d = D(a, [w_gone, P("m2", "mcp", 5, hb=400), a], at=400)
    assert d.action == "fail" and d.message is not None and "Hintergrunddienst" in d.message


def test_role_not_in_request_stays() -> None:
    ai = P("x1", "ai-worker", 4)
    assert D(ai, [ai]).action == "stay"


def test_restart_roles_follow_plugin_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "PLUGIN_ROLES", ("api", "worker", "mcp"))
    assert restart_roles() == ("worker", "mcp", "api")
    monkeypatch.setattr(state, "PLUGIN_ROLES", ("api", "worker", "mcp", "ai-worker"))
    assert restart_roles() == ("worker", "ai-worker", "mcp", "api")


def test_constants() -> None:
    assert restart.RESTART_EXIT_CODE == 75
    assert restart.RESTART_ORDER[-1] == "api"
