"""Process designer validation (plan D12): pure codes plus the ``/validation`` endpoint.

Seed band 896800-896999: only the non-admin id is taken from it; the API test lets the
writer generate all entity ids and deletes every ``wf3 val`` row at the end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCacheInvalidation, TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.process.designer.ids import EntityType
from tiqora.process.designer.schemas import DesignerEntityOut, ProcessDraftOut
from tiqora.process.designer.validation import (
    DfInfo,
    ReferenceData,
    ValidationIssue,
    validate_process,
)
from tiqora.process.designer.yaml_codec import load_znuny_yaml

BASE = "/api/v1/admin/process-designer"
NON_ADMIN = 896800
MOD = "Kernel::System::ProcessManagement::TransitionAction::"
NOW = datetime(2026, 10, 1, 12, 0, 0)


def _id(kind: str, n: int) -> str:
    return f"{kind}-{str(n) * 32}"


def _ent(kind: EntityType, n: int, config: dict[str, Any]) -> DesignerEntityOut:
    return DesignerEntityOut(
        entity_type=kind,
        entity_id=_id(kind, n),
        id=n,
        name=f"wf3 val {kind} {n}",
        config=config,
        version="v",
        changed_at=NOW,
        changed_by=1,
    )


A1, A2, A3 = _id("Activity", 1), _id("Activity", 2), _id("Activity", 3)
D1, D2 = _id("ActivityDialog", 1), _id("ActivityDialog", 2)
T1, T2 = _id("Transition", 1), _id("Transition", 2)
X1 = _id("TransitionAction", 1)

REFS = ReferenceData(
    queues=frozenset({"Raw", "Support"}),
    states=frozenset({"new", "open"}),
    priorities=frozenset({"3 normal"}),
    types=frozenset({"Incident"}),
    services=frozenset({"Mail"}),
    slas=frozenset({"Gold"}),
    users=frozenset({"root@localhost"}),
    calendars=frozenset({"Main"}),
    dynamic_fields={
        "Color": DfInfo("Dropdown", frozenset({"red", "blue"})),
        "Free": DfInfo("Text"),
    },
    ids={
        "queues": frozenset({"1", "2"}),
        "users": frozenset({"1"}),
        "states": frozenset({"1"}),
    },
)

GOOD_TRANSITION = {
    "Condition": {"1": {"Type": "and", "Fields": {"Queue": {"Type": "String", "Match": "Raw"}}}},
    "ConditionLinking": "and",
}
GOOD_DIALOG = {
    "Fields": {"Title": {"Display": 1}},
    "FieldOrder": ["Title"],
    "Interface": ["AgentInterface"],
}


def draft_of(
    *,
    process: dict[str, Any] | None = None,
    activities: dict[int, dict[str, Any]] | None = None,
    dialogs: dict[int, dict[str, Any]] | None = None,
    transitions: dict[int, dict[str, Any]] | None = None,
    actions: dict[int, dict[str, Any]] | None = None,
) -> ProcessDraftOut:
    """A consistent two-step process; overrides replace whole entity groups."""
    cfg: dict[str, Any] = {
        "Description": "",
        "StartActivity": A1,
        "StartActivityDialog": D1,
        "Path": {A1: {T1: {"ActivityEntityID": A2}}, A2: {}},
    }
    cfg.update(process or {})
    acts = activities or {
        1: {"ActivityDialog": {"1": D1}},
        2: {"ActivityDialog": {}},
    }
    return ProcessDraftOut(
        process=_ent("Process", 9, cfg),
        state_entity_id="S2",
        state_name="Inactive",
        layout={},
        activities=[_ent("Activity", n, c) for n, c in acts.items()],
        activity_dialogs=[
            _ent("ActivityDialog", n, c) for n, c in (dialogs or {1: GOOD_DIALOG}).items()
        ],
        transitions=[
            _ent("Transition", n, c) for n, c in (transitions or {1: GOOD_TRANSITION}).items()
        ],
        transition_actions=[_ent("TransitionAction", n, c) for n, c in (actions or {}).items()],
        missing_references=[],
        pending_changes=0,
    )


def codes(
    draft: ProcessDraftOut, *, parallel: bool = False, refs: ReferenceData = REFS
) -> list[str]:
    return [i.code for i in validate_process(draft, refs, parallel_operation=parallel)]


def issue(draft: ProcessDraftOut, code: str, **kw: Any) -> ValidationIssue:
    found = [
        i
        for i in validate_process(
            draft, kw.pop("refs", REFS), parallel_operation=kw.pop("parallel", False)
        )
        if i.code == code
    ]
    assert found, f"{code} not reported"
    return found[0]


def action(module: str, config: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {1: {"Module": MOD + module, "Config": config}}


def with_action(module: str, config: dict[str, Any], **kw: Any) -> ProcessDraftOut:
    path = {A1: {T1: {"ActivityEntityID": A2, "TransitionAction": [X1]}}, A2: {}}
    return draft_of(process={"Path": path}, actions=action(module, config), **kw)


# --- errors ---------------------------------------------------------------------------------


def test_clean_process_has_no_issues() -> None:
    assert codes(draft_of()) == []


def test_start_activity_missing() -> None:
    d = draft_of(process={"StartActivity": ""})
    i = issue(d, "start_activity_missing")
    assert (i.severity, i.entity_type, i.field) == ("error", "Process", "StartActivity")


def test_start_dialog_missing() -> None:
    assert (
        issue(draft_of(process={"StartActivityDialog": ""}), "start_dialog_missing").severity
        == "error"
    )


def test_start_dialog_not_in_start_activity() -> None:
    d = draft_of(
        dialogs={1: GOOD_DIALOG, 2: GOOD_DIALOG},
        process={"StartActivityDialog": D2},
    )
    i = issue(d, "start_dialog_not_in_start_activity")
    assert i.params["id"] == D2 and i.params["activity"] == A1


def test_reference_missing_for_unknown_target_dialog_and_action() -> None:
    ghost = _id("Activity", 7)
    path = {A1: {T1: {"ActivityEntityID": ghost, "TransitionAction": [X1]}}, A2: {}}
    d = draft_of(process={"Path": path})
    found = [
        i
        for i in validate_process(d, REFS, parallel_operation=False)
        if i.code == "reference_missing"
    ]
    assert {(i.params["kind"], i.params["id"]) for i in found} == {
        ("Activity", ghost),
        ("TransitionAction", X1),
    }
    d2 = draft_of(activities={1: {"ActivityDialog": {"1": D1, "2": D2}}, 2: {"ActivityDialog": {}}})
    i = issue(d2, "reference_missing")
    assert (i.entity_type, i.params["id"], i.field) == ("Activity", D2, "ActivityDialog.2")


def test_dialog_no_interface() -> None:
    d = draft_of(dialogs={1: {**GOOD_DIALOG, "Interface": []}})
    assert issue(d, "dialog_no_interface").entity_id == D1


def test_transition_target_missing() -> None:
    d = draft_of(process={"Path": {A1: {T1: {"ActivityEntityID": ""}}, A2: {}}})
    i = issue(d, "transition_target_missing")
    assert i.params["transition"] == T1


def test_condition_value_impossible_name_possible_values_and_regexp() -> None:
    def cond(fields: dict[str, Any]) -> dict[int, dict[str, Any]]:
        return {
            1: {"Condition": {"1": {"Type": "and", "Fields": fields}}, "ConditionLinking": "and"}
        }

    queue = draft_of(transitions=cond({"Queue": {"Type": "Equal", "Match": ["Raw", "Nope"]}}))
    i = issue(queue, "condition_value_impossible")
    assert i.params["value"] == "Nope" and i.entity_id == T1
    assert i.field == "Condition.1.Fields.Queue"

    df = draft_of(transitions=cond({"DynamicField_Color": {"Type": "String", "Match": "green"}}))
    assert issue(df, "condition_value_impossible").params["reason"] == "possible_values"
    ok = draft_of(transitions=cond({"DynamicField_Color": {"Type": "String", "Match": "red"}}))
    assert codes(ok) == []
    free = draft_of(transitions=cond({"DynamicField_Free": {"Type": "String", "Match": "x"}}))
    assert codes(free) == []

    rx = draft_of(transitions=cond({"Title": {"Type": "Regexp", "Match": "(unclosed"}}))
    assert issue(rx, "condition_value_impossible").params["reason"] == "regexp"
    tagged = draft_of(transitions=cond({"Queue": {"Type": "String", "Match": "<OTRS_X>"}}))
    assert codes(tagged) == []


def test_action_reference_missing_names_ids_and_smart_tags() -> None:
    d = with_action("TicketQueueSet", {"Queue": "Ghost"})
    i = issue(d, "action_reference_missing")
    assert (i.entity_id, i.field, i.params["value"]) == (X1, "Config.Queue", "Ghost")
    assert codes(with_action("TicketQueueSet", {"Queue": "Raw"})) == []
    assert codes(with_action("TicketQueueSet", {"QueueID": "2"})) == []
    assert "action_reference_missing" in codes(with_action("TicketQueueSet", {"QueueID": "99"}))
    assert codes(with_action("TicketQueueSet", {"Queue": "<OTRS_TICKET_Queue>"})) == []
    assert "action_reference_missing" in codes(with_action("TicketOwnerSet", {"Owner": "nobody"}))
    assert "action_reference_missing" in codes(
        with_action("DynamicFieldPendingTimeSet", {"DynamicField": "Nope"})
    )
    assert "action_reference_missing" in codes(
        with_action("DynamicFieldSet", {"Nope": "1", "Color": "red"})
    )
    assert codes(with_action("DynamicFieldSet", {"Color": "red", "UserID": "1"})) == []


def test_action_required_param_missing() -> None:
    i = issue(with_action("TicketStateSet", {}), "action_required_param_missing")
    assert i.params["param"] == "State / StateID"
    assert "action_required_param_missing" in codes(with_action("TicketStateSet", {"State": ""}))
    assert codes(with_action("TicketStateSet", {"StateID": "1"})) == []


def test_article_send_without_from_is_valid() -> None:
    # Znuny ArticleSend.pm:162 always replaces From via FromGet (queue address fallback), so a
    # missing From is how imported Znuny processes look; it must not be reported as an error.
    config = {
        "SenderType": "agent",
        "IsVisibleForCustomer": "1",
        "To": "customer@example.org",
        "Subject": "Hello",
        "Body": "Text",
    }
    assert codes(with_action("ArticleSend", config)) == []


# --- warnings -------------------------------------------------------------------------------


def test_activity_unreachable() -> None:
    d = draft_of(
        activities={
            1: {"ActivityDialog": {"1": D1}},
            2: {"ActivityDialog": {}},
            3: {"ActivityDialog": {}},
        },
        process={"Path": {A1: {T1: {"ActivityEntityID": A2}}, A2: {}, A3: {}}},
    )
    i = issue(d, "activity_unreachable")
    assert (i.severity, i.entity_id) == ("warning", A3)


def test_activity_dead_end() -> None:
    d = draft_of(process={"Path": {A1: {}, A2: {}}})
    assert issue(d, "activity_dead_end").entity_id == A1


def test_dialog_no_fields_and_field_unknown_and_required_display() -> None:
    assert (
        issue(draft_of(dialogs={1: {**GOOD_DIALOG, "Fields": {}}}), "dialog_no_fields").severity
        == "warning"
    )
    d = draft_of(
        dialogs={
            1: {
                **GOOD_DIALOG,
                "Fields": {
                    "DynamicField_Gone": {"Display": 1},
                    "DynamicField_Color": {"Display": 1},
                    "Queue": {"Display": 1},
                    "State": {"Display": 2},
                },
            }
        }
    )
    found = validate_process(d, REFS, parallel_operation=False)
    assert [(i.code, i.field) for i in found] == [
        ("dialog_field_unknown", "Fields.DynamicField_Gone"),
        ("queue_required_display", "Fields.Queue"),
    ]


def test_condition_module_unknown_and_known() -> None:
    def mod(match: str) -> dict[int, dict[str, Any]]:
        fields = {"Queue": {"Type": "Module", "Match": match}}
        return {
            1: {"Condition": {"1": {"Type": "and", "Fields": fields}}, "ConditionLinking": "and"}
        }

    prefix = "Kernel::System::ProcessManagement::TransitionValidation::"
    assert (
        issue(draft_of(transitions=mod(prefix + "Nope")), "condition_module_unknown")
        .params["module"]
        .endswith("Nope")
    )
    assert codes(draft_of(transitions=mod(prefix + "ValidateDemo.pm"))) == []


def test_condition_type_unknown_but_array_hash_accepted() -> None:
    def typed(t: str) -> dict[int, dict[str, Any]]:
        fields = {"Title": {"Type": t, "Match": "x"}}
        return {
            1: {"Condition": {"1": {"Type": "and", "Fields": fields}}, "ConditionLinking": "and"}
        }

    assert (
        issue(draft_of(transitions=typed("Fuzzy")), "condition_type_unknown").params["type"]
        == "Fuzzy"
    )
    assert codes(draft_of(transitions=typed("Array"))) == []
    assert codes(draft_of(transitions=typed("Hash"))) == []


def test_action_module_unsupported() -> None:
    d = with_action("ConfigItemUpdate", {"ConfigItemID": "1"})
    unsupported = [
        i
        for i in validate_process(d, REFS, parallel_operation=False)
        if i.code == "action_module_unsupported"
    ]
    # registered but with run=None, or unknown to the registry: both warn
    unknown = with_action("TotallyMadeUp", {})
    assert issue(unknown, "action_module_unsupported").params["module"] == "TotallyMadeUp"
    assert all(i.severity == "warning" for i in unsupported)


def test_tiqora_only_module_parallel_only_when_parallel() -> None:
    from tiqora.extensions.core import get_registry, set_registry_for_tests
    from tiqora.extensions.registry import Registry
    from tiqora.process.actions.registry import TRANSITION_ACTIONS, TransitionActionModule

    async def _noop(*_a: Any) -> None:
        return None

    registry = Registry()
    registry.add(
        TRANSITION_ACTIONS,
        TransitionActionModule(name="TiqoraDemo", run=_noop, tiqora_only=True),
    )
    previous = get_registry()
    set_registry_for_tests(registry)
    try:
        d = with_action("TiqoraDemo", {})
        assert "tiqora_only_module_parallel" not in codes(d, parallel=False)
        assert "action_module_unsupported" not in codes(d, parallel=True)
        i = issue(d, "tiqora_only_module_parallel", parallel=True)
        assert (i.severity, i.entity_id, i.params["module"]) == ("warning", X1, "TiqoraDemo")
    finally:
        set_registry_for_tests(previous)


def test_transition_without_condition() -> None:
    d = draft_of(transitions={1: {"Condition": {}, "ConditionLinking": "and"}})
    i = issue(d, "transition_without_condition")
    assert (i.severity, i.entity_id) == ("warning", T1)


def test_errors_sort_before_warnings() -> None:
    d = draft_of(process={"StartActivity": ""}, transitions={1: {"Condition": {}}})
    severities = [i.severity for i in validate_process(d, REFS, parallel_operation=False)]
    assert severities == sorted(severities, key={"error": 0, "warning": 1}.get)
    assert "error" in severities and "warning" in severities


# --- a Znuny-shaped export -------------------------------------------------------------------

SAMPLE_YAML = """---
Activity:
  Activity-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:
    ActivityDialog:
      '1': ActivityDialog-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    Name: Order received
  Activity-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:
    ActivityDialog:
      '1': ActivityDialog-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
    Name: Approved
  Activity-cccccccccccccccccccccccccccccccc:
    ActivityDialog: {}
    Name: Done
ActivityDialog:
  ActivityDialog-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:
    DescriptionShort: Order
    Fields:
      Article:
        DefaultValue: ''
        Display: 1
      DynamicField_Color:
        Display: 1
      Title:
        Display: 1
    FieldOrder: [Title, Article, DynamicField_Color]
    Interface: [AgentInterface, CustomerInterface]
    Name: Order
    Permission: ''
  ActivityDialog-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:
    DescriptionShort: Approve
    Fields:
      State:
        DefaultValue: open
        Display: 2
    FieldOrder: [State]
    Interface: [AgentInterface]
    Name: Approve
    Permission: ''
Process:
  Process-dddddddddddddddddddddddddddddddd:
    Activities: []
    Description: Sample
    Name: Order process
    Path:
      Activity-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:
        Transition-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:
          ActivityEntityID: Activity-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
          TransitionAction: [TransitionAction-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa]
      Activity-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:
        Transition-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:
          ActivityEntityID: Activity-cccccccccccccccccccccccccccccccc
      Activity-cccccccccccccccccccccccccccccccc: {}
    StartActivity: Activity-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    StartActivityDialog: ActivityDialog-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    State: Active
Transition:
  Transition-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:
    Condition:
      '1':
        Fields:
          Queue: {Match: Raw, Type: String}
          DynamicField_Color: {Match: red, Type: String}
        Type: and
    ConditionLinking: and
    Name: Raw and red
  Transition-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:
    Condition:
      '1':
        Fields:
          Title: {Match: '^Order', Type: Regexp}
        Type: and
    ConditionLinking: and
    Name: Order title
TransitionAction:
  TransitionAction-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:
    Config:
      State: open
    Module: Kernel::System::ProcessManagement::TransitionAction::TicketStateSet
    Name: Open it
"""


def _draft_from_export(raw: str) -> ProcessDraftOut:
    data = load_znuny_yaml(raw)

    def ents(kind: EntityType) -> list[DesignerEntityOut]:
        return [
            DesignerEntityOut(
                entity_type=kind,
                entity_id=eid,
                id=n,
                name=str(cfg.get("Name", eid)),
                config={k: v for k, v in cfg.items() if k != "Name"},
                version="v",
                changed_at=NOW,
                changed_by=1,
            )
            for n, (eid, cfg) in enumerate(data.get(kind, {}).items(), start=1)
        ]

    process = ents("Process")[0]
    return ProcessDraftOut(
        process=process,
        state_entity_id="S1",
        state_name="Active",
        layout={},
        activities=ents("Activity"),
        activity_dialogs=ents("ActivityDialog"),
        transitions=ents("Transition"),
        transition_actions=ents("TransitionAction"),
        missing_references=[],
        pending_changes=0,
    )


def test_znuny_shaped_export_validates_without_errors() -> None:
    found = validate_process(_draft_from_export(SAMPLE_YAML), REFS, parallel_operation=True)
    assert [i for i in found if i.severity == "error"] == []
    assert [i.code for i in found] == []
    broken = SAMPLE_YAML.replace("Match: red", "Match: purple")
    errors = [
        i.code
        for i in validate_process(_draft_from_export(broken), REFS, parallel_operation=True)
        if i.severity == "error"
    ]
    assert errors == ["condition_value_impossible"]


# --- API (both dialects) ----------------------------------------------------------------------

_URLS: set[str] = set()
_TABLES = (
    "pm_process",
    "pm_activity",
    "pm_activity_dialog",
    "pm_transition",
    "pm_transition_action",
)


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _purge(sync_url: str, signal_mark: int | None = None) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for table in _TABLES:
            ids = conn.execute(
                text(f"SELECT entity_id FROM {table} WHERE name LIKE 'wf3 val%'")
            ).scalars()
            for eid in list(ids):
                conn.execute(text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": eid})
            conn.execute(text(f"DELETE FROM {table} WHERE name LIKE 'wf3 val%'"))
        if signal_mark is not None:
            conn.execute(
                text("DELETE FROM tiqora_cache_invalidation WHERE id > :m"), {"m": signal_mark}
            )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _final_cleanup() -> Generator[None, None, None]:
    yield
    for sync_url in _URLS:
        _purge(sync_url)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    sync_engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(
        sync_engine,
        tables=[TiqoraCacheInvalidation.__table__, TiqoraSettings.__table__],  # type: ignore[list-item]
        checkfirst=True,
    )
    with sync_engine.connect() as conn:
        mark = int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
            ).scalar_one()
        )
    sync_engine.dispose()
    _purge(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()
    _purge(sync_url, mark)


@pytest.fixture
def make_client(factory: async_sessionmaker[AsyncSession]) -> Any:
    @asynccontextmanager
    async def _make(user_id: int = 1) -> AsyncIterator[AsyncClient]:
        async def _db() -> Any:
            async with factory() as session:
                yield session

        user = AuthenticatedUser(
            id=user_id,
            login="wf3@example.org",
            first_name="W",
            last_name="V",
            auth_method="session",
        )
        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = _db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    return _make


@pytest.mark.db
async def test_validation_endpoint_both_dialects(make_client: Any) -> None:
    async with make_client(NON_ADMIN) as client:
        resp = await client.get(f"{BASE}/processes/{_id('Process', 1)}/validation")
        assert resp.status_code == 403

    async with make_client() as client:
        assert (
            await client.get(f"{BASE}/processes/{_id('Process', 1)}/validation")
        ).status_code == 404

        pid = (await client.post(f"{BASE}/processes", json={"name": "wf3 val p"})).json()[
            "process"
        ]["entity_id"]
        empty = (await client.get(f"{BASE}/processes/{pid}/validation")).json()
        assert [i["code"] for i in empty] == ["start_activity_missing", "start_dialog_missing"]
        assert empty[0]["entity_type"] == "Process" and empty[0]["entity_id"] == pid
        assert empty[0]["severity"] == "error" and empty[0]["field"] == "StartActivity"

        async def make(kind: str, name: str, config: dict[str, Any]) -> dict[str, Any]:
            resp = await client.post(
                f"{BASE}/entities/{kind}", json={"name": name, "config": config}
            )
            assert resp.status_code == 201, resp.text
            return resp.json()

        dialog = await make("ActivityDialog", "wf3 val dialog", GOOD_DIALOG)
        start = await make(
            "Activity", "wf3 val start", {"ActivityDialog": {"1": dialog["entity_id"]}}
        )
        end = await make("Activity", "wf3 val end", {"ActivityDialog": {}})
        transition = await make(
            "Transition",
            "wf3 val transition",
            {
                "Condition": {
                    "1": {
                        "Type": "and",
                        "Fields": {"Queue": {"Type": "String", "Match": "wf3 val no such queue"}},
                    }
                },
                "ConditionLinking": "and",
            },
        )
        process = (await client.get(f"{BASE}/processes/{pid}")).json()["process"]
        updated = await client.put(
            f"{BASE}/processes/{pid}",
            json={
                "expected_version": process["version"],
                "name": "wf3 val p",
                "description": "",
                "state_entity_id": "S2",
                "start_activity": start["entity_id"],
                "start_activity_dialog": dialog["entity_id"],
                "path": {
                    start["entity_id"]: {
                        transition["entity_id"]: {"ActivityEntityID": end["entity_id"]}
                    },
                    end["entity_id"]: {},
                },
                "layout": {},
            },
        )
        assert updated.status_code == 200, updated.text

        issues = (await client.get(f"{BASE}/processes/{pid}/validation")).json()
        assert [(i["code"], i["entity_id"], i["field"]) for i in issues] == [
            (
                "condition_value_impossible",
                transition["entity_id"],
                "Condition.1.Fields.Queue",
            )
        ]
        assert issues[0]["severity"] == "error"
        assert issues[0]["params"]["value"] == "wf3 val no such queue"
