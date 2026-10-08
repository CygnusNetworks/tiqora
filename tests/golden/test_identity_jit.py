"""Golden-master: a JIT-provisioned agent is a normal Znuny agent (wave 1, Task 17).

``provision_agent`` + a role grant run against the peer's database; the peer's own
``User`` / ``Group`` objects then have to read the account back. Run with
``GOLDEN=1 just golden-run`` (needs the peer container up).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from _helpers import znuny_perl_eval

pytestmark = pytest.mark.golden

LOGIN = "idp.golden.jit"
ROLE = "idp-golden-jit-role"

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

_READ_BACK = (
    _PRELUDE
    + rf"""
my %U = $Kernel::OM->Get('Kernel::System::User')->GetUserData(User => '{LOGIN}');
my %Roles = $Kernel::OM->Get('Kernel::System::Group')->PermissionRoleUserGet(
    UserID => $U{{UserID}},
);
print "$U{{UserEmail}}|$U{{ValidID}}|" . join(',', sort keys %Roles);
"""
)


async def test_provisioned_agent_is_readable_by_znuny(golden_session_factory) -> None:
    from tiqora.domain.user_delete import delete_user_rows
    from tiqora.domain.user_provisioning import grant_role
    from tiqora.identity.config_models import JitDefaults
    from tiqora.identity.mapping import MappedProfile
    from tiqora.identity.provisioning import provision_agent

    role_id = int(znuny_perl_eval(_ROLE_ADD).strip())
    provider = SimpleNamespace(match_by="login", jit_defaults=JitDefaults())
    user_id: int | None = None
    try:
        async with golden_session_factory() as session:
            user = await provision_agent(
                session,
                provider,  # type: ignore[arg-type]
                MappedProfile(
                    login=LOGIN,
                    email="jit@example.test",
                    first_name="Golden",
                    last_name="Jit",
                ),
            )
            user_id = user.id
            await grant_role(session, user_id=user_id, role_id=role_id, actor_id=1)
            await session.commit()
        out = znuny_perl_eval(_READ_BACK).strip()
        assert out == f"jit@example.test|1|{role_id}"
    finally:
        async with golden_session_factory() as session:
            if user_id is not None:
                await delete_user_rows(session, user_id)
            await session.commit()
        znuny_perl_eval(
            _PRELUDE
            + rf"""
my $DBObject = $Kernel::OM->Get('Kernel::System::DB');
$DBObject->Do(SQL => 'DELETE FROM roles WHERE id = ?', Bind => [\{role_id}]);
print 'ok';
"""
        )
