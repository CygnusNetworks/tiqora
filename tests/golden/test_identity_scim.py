"""Golden-master: a SCIM-provisioned agent is a normal Znuny agent (wave 4, Task 22).

``create_user`` + a SCIM group whose rule grants a role + ``run_scim_role_sync_tick`` run against
the peer's database; the peer's own ``User`` / ``Group`` objects then have to read the account
and its role back, and ``patch_user`` with ``active=false`` has to show up as ``ValidID 2``.
Seed band 63395-63399 (login ``sc63.golden.scim@example.test``, provider slug ``sc63golden``).
Run with ``GOLDEN=1 just golden-run`` (needs the peer container up).
"""

from __future__ import annotations

import pytest
from _helpers import znuny_perl_eval
from sqlalchemy import text

pytestmark = pytest.mark.golden

LOGIN = "sc63.golden.scim@example.test"
ROLE = "sc63-golden-scim-role"
SLUG = "sc63golden"
KEY = "golden-scim-secret-key"
BASE = "https://helpdesk.example.test/scim/v2"

_PRELUDE = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-master'},
);
"""

_ROLE_ADD = (
    _PRELUDE
    + rf"""
my $GroupObject = $Kernel::OM->Get('Kernel::System::Group');
my $RoleID = $GroupObject->RoleAdd(
    Name => '{ROLE}', Comment => 'golden', ValidID => 1, UserID => 1,
);
print $RoleID;
"""
)

_READ_ACTIVE = (
    _PRELUDE
    + rf"""
$Kernel::OM->Get('Kernel::System::Cache')->CleanUp(Type => 'User');
my %U = $Kernel::OM->Get('Kernel::System::User')->GetUserData(User => '{LOGIN}');
$Kernel::OM->Get('Kernel::System::Cache')->CleanUp(Type => 'DBRoleUserGet');
my %Roles = $Kernel::OM->Get('Kernel::System::Group')->PermissionUserRoleGet(
    UserID => $U{{UserID}},
);
print "$U{{UserEmail}}|$U{{ValidID}}|" . join(',', sort keys %Roles);
"""
)

_READ_VALID = (
    _PRELUDE
    + rf"""
$Kernel::OM->Get('Kernel::System::Cache')->CleanUp(Type => 'User');
my %U = $Kernel::OM->Get('Kernel::System::User')->GetUserData(User => '{LOGIN}');
print $U{{ValidID}};
"""
)

_DELETE_ROLE = (
    _PRELUDE
    + r"""
my $DBObject = $Kernel::OM->Get('Kernel::System::DB');
$DBObject->Do(SQL => 'DELETE FROM role_user WHERE role_id = ?', Bind => [\__ROLE_ID__]);
$DBObject->Do(SQL => 'DELETE FROM roles WHERE id = ?', Bind => [\__ROLE_ID__]);
$Kernel::OM->Get('Kernel::System::Cache')->CleanUp(Type => 'DBRoleUserGet');
print 'ok';
"""
)

_SCIM_TABLES = (
    "DELETE FROM tiqora_scim_event WHERE provider_id IN {prov}",
    "DELETE FROM tiqora_scim_pending_sync WHERE provider_id IN {prov}",
    "DELETE FROM tiqora_scim_group_member WHERE group_id IN"
    " (SELECT id FROM tiqora_scim_group WHERE provider_id IN {prov})",
    "DELETE FROM tiqora_scim_group WHERE provider_id IN {prov}",
    "DELETE FROM tiqora_scim_user WHERE provider_id IN {prov}",
    "DELETE FROM tiqora_scim_token WHERE provider_id IN {prov}",
    "DELETE FROM tiqora_identity_managed_assignment WHERE provider_id IN {prov}",
    "DELETE FROM tiqora_identity_link WHERE provider_id IN {prov}",
)


async def _purge(golden_session_factory, role_id: int | None) -> None:  # type: ignore[no-untyped-def]
    from tiqora.domain.user_delete import delete_user_rows

    prov = "(SELECT id FROM tiqora_identity_provider WHERE slug = :slug)"
    async with golden_session_factory() as session:
        user_id = (
            await session.execute(text("SELECT id FROM users WHERE login = :l"), {"l": LOGIN})
        ).scalar_one_or_none()
        for stmt in _SCIM_TABLES:
            await session.execute(text(stmt.format(prov=prov)), {"slug": SLUG})
        await session.execute(
            text("DELETE FROM tiqora_identity_provider WHERE slug = :slug"), {"slug": SLUG}
        )
        if user_id is not None:
            await delete_user_rows(session, int(user_id))
        await session.commit()
    if role_id is not None:
        znuny_perl_eval(_DELETE_ROLE.replace("__ROLE_ID__", str(role_id)))


async def test_scim_provisioned_agent_is_a_znuny_agent(
    golden_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.config import Settings
    from tiqora.identity.config_models import RoleRule
    from tiqora.identity.scim import role_sync
    from tiqora.identity.scim.groups import create_group
    from tiqora.identity.scim.patch import parse_patch
    from tiqora.identity.scim.protocol import SCHEMA_PATCH
    from tiqora.identity.scim.role_sync import run_scim_role_sync_tick
    from tiqora.identity.scim.tokens import ScimTenant
    from tiqora.identity.scim.users import create_user, patch_user
    from tiqora.identity.store import ProviderChanges, create_provider, update_provider

    settings = Settings(environment="test", secret_key=KEY)
    # The tick opens its own sessions: point them at the peer's database.
    monkeypatch.setattr(role_sync, "get_session_factory", lambda: golden_session_factory)
    monkeypatch.setattr(role_sync, "get_settings", lambda: settings)

    await _purge(golden_session_factory, None)
    role_id = int(znuny_perl_eval(_ROLE_ADD).strip())
    try:
        async with golden_session_factory() as session:
            created = await create_provider(
                session,
                secret_key=KEY,
                actor_id=1,
                preset="generic_oidc",
                name="Golden SCIM",
                slug=SLUG,
                audience="agent",
            )
            provider = await update_provider(
                session,
                created.id,
                ProviderChanges(
                    enabled=True,
                    scim_enabled=True,
                    role_rules=[
                        RoleRule(attribute="scim:groups", value="Golden", role_ids=[role_id])
                    ],
                ),
                secret_key=KEY,
                actor_id=1,
                settings=settings,
            )
            tenant = ScimTenant(provider=provider, user_kind="agent", token_id=1)
            user = await create_user(
                session,
                tenant,
                {
                    "userName": LOGIN,
                    "name": {"givenName": "Golden", "familyName": "Scim"},
                    "emails": [{"value": LOGIN, "primary": True}],
                    "active": True,
                },
                base=BASE,
            )
            await create_group(
                session,
                tenant,
                {"displayName": "Golden", "members": [{"value": user.resource["id"]}]},
                base=BASE,
                max_members=5000,
            )
            await session.commit()

        counts = await run_scim_role_sync_tick()
        assert counts["errors"] == 0
        assert counts["processed"] >= 1
        assert znuny_perl_eval(_READ_ACTIVE).strip() == f"{LOGIN}|1|{role_id}"

        async with golden_session_factory() as session:
            result = await patch_user(
                session,
                tenant,
                user.resource["id"],
                parse_patch(
                    {
                        "schemas": [SCHEMA_PATCH],
                        "Operations": [{"op": "replace", "path": "active", "value": False}],
                    }
                ),
                base=BASE,
            )
            await session.commit()
        assert result.deactivated is True
        assert znuny_perl_eval(_READ_VALID).strip() == "2"
    finally:
        await _purge(golden_session_factory, role_id)
