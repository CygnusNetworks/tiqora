"""Pure attribute mapping, role-rule evaluation and managed-assignment diff (D6).

No I/O, no DB. All functions are total: odd input never raises.

``has_role_rules`` / ``has_group_rules`` (not defined in D6 itself): True when at
least one configured rule carries role ids / group grants, independent of whether
it matches this user or whether its ids are still valid. A kind without any rule
is never touched by the diff; a kind with rules that match nothing is "desired
empty" (so authoritative mode strips it, managed mode only strips managed rows).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any, Final

from tiqora.identity.config_models import AttributeMap, RoleRule
from tiqora.identity.types import SyncMode

Attributes = Mapping[str, tuple[str, ...]]

MAX_VALUE_LEN: Final = 2000
LDAP_GROUP_DN_ATTR: Final = "ldap:group_dn"


def _scalar(v: Any) -> str | None:
    if isinstance(v, str):
        s = v.strip()
    elif isinstance(v, (bool, int, float)):
        s = str(v)
    else:
        return None
    return s[:MAX_VALUE_LEN] if s else None


def normalize_attributes(raw: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Flatten raw IdP/LDAP attributes to ``name -> tuple[str, ...]`` (empty/odd values dropped)."""
    out: dict[str, tuple[str, ...]] = {}
    try:
        items = list(raw.items())
    except Exception:
        return out
    for key, value in items:
        items_: Iterable[Any] = (
            value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
        )
        vals = tuple(s for s in (_scalar(x) for x in items_) if s is not None)
        if vals:
            out[str(key)] = vals
    return out


def lookup(attributes: Attributes, name: str) -> tuple[str, ...]:
    """Exact key, else the single case-insensitive match; ambiguous or missing -> ``()``."""
    if name in attributes:
        return tuple(attributes[name])
    folded = name.lower()
    hits = [k for k in attributes if k.lower() == folded]
    return tuple(attributes[hits[0]]) if len(hits) == 1 else ()


@dataclass(frozen=True, slots=True)
class MappedProfile:
    login: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    customer_id: str | None = None
    mobile: str | None = None


def _first(attributes: Attributes, names: Sequence[str]) -> str | None:
    for name in names:
        if name.startswith("_"):  # Znuny UserSyncMap convention: "_text" is the fixed value "text"
            literal = name[1:].strip()
            if literal:
                return literal
            continue
        for v in lookup(attributes, name):
            if v:
                return v
    return None


def map_profile(attributes: Attributes, attribute_map: AttributeMap) -> MappedProfile:
    return MappedProfile(
        login=_first(attributes, attribute_map.login),
        email=_first(attributes, attribute_map.email),
        first_name=_first(attributes, attribute_map.first_name),
        last_name=_first(attributes, attribute_map.last_name),
        customer_id=_first(attributes, attribute_map.customer_id),
        mobile=_first(attributes, attribute_map.mobile),
    )


def normalize_dn(value: str) -> str:
    """Lower-case and strip whitespace around ``,`` and ``=``.

    Limitation: a backslash-escaped comma inside an RDN value (``CN=Doe\\, Jane``)
    is split like a separator; both sides of a comparison are treated alike, so
    equal DNs still match, but whitespace after an escaped comma is also stripped.
    """
    parts = [p.strip() for p in value.strip().lower().split(",")]
    return ",".join("=".join(q.strip() for q in p.split("=")) for p in parts)


def rule_matches(attributes: Attributes, rule: RoleRule) -> bool:
    is_dn = rule.attribute.strip().lower() == LDAP_GROUP_DN_ATTR

    def norm(s: str) -> str:
        return normalize_dn(s) if is_dn else s.strip().lower()

    wanted = norm(rule.value)
    return any(norm(v) == wanted for v in lookup(attributes, rule.attribute.strip()))


@dataclass(frozen=True, slots=True)
class DesiredAssignments:
    role_ids: frozenset[int]
    group_perms: frozenset[tuple[int, str]]
    has_role_rules: bool
    has_group_rules: bool


def evaluate_rules(
    attributes: Attributes,
    rules: Sequence[RoleRule],
    *,
    valid_role_ids: AbstractSet[int],
    valid_group_ids: AbstractSet[int],
) -> DesiredAssignments:
    """Union over matching rules; invalid ids dropped; ``rw`` per group wins over every other key.

    The ``rw`` collapse applies to the merged permission set of a group across all
    matching rules (Znuny: "if rw permission exists, discard all other").
    """
    roles: set[int] = set()
    per_group: dict[int, set[str]] = {}
    for rule in rules:
        if not rule_matches(attributes, rule):
            continue
        roles.update(r for r in rule.role_ids if r in valid_role_ids)
        for grant in rule.groups:
            if grant.group_id in valid_group_ids and grant.permissions:
                per_group.setdefault(grant.group_id, set()).update(grant.permissions)
    perms = frozenset(
        (gid, p) for gid, ps in per_group.items() for p in (("rw",) if "rw" in ps else sorted(ps))
    )
    return DesiredAssignments(
        role_ids=frozenset(roles),
        group_perms=perms,
        has_role_rules=any(r.role_ids for r in rules),
        has_group_rules=any(r.groups for r in rules),
    )


@dataclass(frozen=True, slots=True)
class AssignmentState:
    current_roles: frozenset[int]
    current_group_perms: frozenset[tuple[int, str]]
    managed_roles: frozenset[int]
    managed_group_perms: frozenset[tuple[int, str]]


@dataclass(frozen=True, slots=True)
class AssignmentDiff:
    add_roles: frozenset[int] = frozenset()
    remove_roles: frozenset[int] = frozenset()
    add_groups: frozenset[tuple[int, str]] = frozenset()
    remove_groups: frozenset[tuple[int, str]] = frozenset()
    record_managed_roles: frozenset[int] = frozenset()
    drop_managed_roles: frozenset[int] = frozenset()
    record_managed_groups: frozenset[tuple[int, str]] = frozenset()
    drop_managed_groups: frozenset[tuple[int, str]] = frozenset()
    # Final permission key set for every group whose keys change (group_id -> keys;
    # an empty set means "no rows left"). Consumers (the Task 18 applier) MUST write
    # each such group's key set ONCE as a replace (Znuny PermissionGroupUserAdd
    # semantics), not apply add_groups / remove_groups rows independently.
    final_group_keys: Mapping[int, frozenset[str]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (
            self.add_roles
            or self.remove_roles
            or self.add_groups
            or self.remove_groups
            or self.record_managed_roles
            or self.drop_managed_roles
            or self.record_managed_groups
            or self.drop_managed_groups
        )


def _diff_set[T](
    desired: frozenset[T], current: frozenset[T], managed: frozenset[T], mode: SyncMode
) -> tuple[frozenset[T], frozenset[T], frozenset[T], frozenset[T]]:
    """Return (add, remove, record_managed, drop_managed) for one kind."""
    add = desired - current
    drop = managed - desired
    if mode == "authoritative":
        return add, current - desired, desired - managed, drop
    return add, drop & current, add, drop


def _keys(perms: AbstractSet[tuple[int, str]], gid: int) -> frozenset[str]:
    return frozenset(p for g, p in perms if g == gid)


def _managed_group_diff(
    desired: DesiredAssignments, state: AssignmentState
) -> tuple[
    frozenset[tuple[int, str]],
    frozenset[tuple[int, str]],
    frozenset[tuple[int, str]],
    frozenset[tuple[int, str]],
]:
    """Managed-mode group diff: never removes or downgrades a key it does not manage.

    Per group: if the current keys already satisfy the desired ones (a present ``rw``
    covers every key) nothing is added. Otherwise the final key set is
    desired ∪ current manual (unmanaged) keys, collapsed to ``{rw}`` when it holds
    ``rw`` (Znuny stores ``rw`` alone). Managed keys no longer desired are removed.
    """
    add: set[tuple[int, str]] = set()
    rem: set[tuple[int, str]] = set()
    gids = {g for g, _ in desired.group_perms} | {g for g, _ in state.managed_group_perms}
    for gid in gids:
        want = _keys(desired.group_perms, gid)
        cur = _keys(state.current_group_perms, gid)
        managed = _keys(state.managed_group_perms, gid)
        satisfied = not want or "rw" in cur or want <= cur
        if satisfied:
            final = cur - (managed - want)
        else:
            final = want | (cur - managed)
            if "rw" in final:
                final = frozenset({"rw"})
        add.update((gid, p) for p in final - cur)
        rem.update((gid, p) for p in cur - final)
    drop = state.managed_group_perms - desired.group_perms
    return frozenset(add), frozenset(rem), frozenset(add), drop


def diff_assignments(
    desired: DesiredAssignments, state: AssignmentState, *, mode: SyncMode
) -> AssignmentDiff:
    """Compute the changes that bring ``state`` to ``desired`` (see module doc for rule-less kinds).

    Group permissions follow Znuny ``PermissionGroupUserAdd``, which replaces the whole
    key set of a (user, group): apply ``final_group_keys`` once per group instead of the
    add/remove rows. Managed mode keeps every unmanaged key (a manual ``rw`` is never
    downgraded); authoritative mode makes the group's keys exactly the desired ones.

    A group rule whose group ids are all invalid (e.g. the group was deleted) still
    counts as "group rules configured": desired is empty, so authoritative mode strips
    the user's group permissions while managed mode only strips managed ones.
    """
    kw: dict[str, Any] = {}
    if desired.has_role_rules:
        r_add, r_rem, r_rec, r_drop = _diff_set(
            desired.role_ids, state.current_roles, state.managed_roles, mode
        )
        kw.update(
            add_roles=r_add,
            remove_roles=r_rem,
            record_managed_roles=r_rec,
            drop_managed_roles=r_drop,
        )
    if desired.has_group_rules:
        if mode == "authoritative":
            g_add, g_rem, g_rec, g_drop = _diff_set(
                desired.group_perms, state.current_group_perms, state.managed_group_perms, mode
            )
        else:
            g_add, g_rem, g_rec, g_drop = _managed_group_diff(desired, state)
        touched = {gid for gid, _ in g_add | g_rem}
        final: dict[int, frozenset[str]] = {}
        for gid in touched:
            keys = {p for g, p in state.current_group_perms if g == gid}
            keys -= {p for g, p in g_rem if g == gid}
            keys |= {p for g, p in g_add if g == gid}
            final[gid] = frozenset(keys)
        # Every key that disappears also loses its managed mark.
        g_drop = g_drop | (g_rem & state.managed_group_perms)
        kw.update(
            add_groups=g_add,
            remove_groups=g_rem,
            record_managed_groups=g_rec,
            drop_managed_groups=g_drop,
        )
        return AssignmentDiff(final_group_keys=final, **kw)
    return AssignmentDiff(**kw)
