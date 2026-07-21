"""Admin CRUD for customer_user / customer_company + customer_user_customer assignment."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import (
    CUSTOMER_COMPANY_CACHE_TYPES,
    CUSTOMER_USER_CACHE_TYPES,
    CUSTOMER_USER_GROUP_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import (
    ListParamsDep,
    Page,
    apply_valid_filter,
    paginate,
)
from tiqora.api.v1.admin.schemas import (
    CustomerCompanyCreate,
    CustomerCompanyOut,
    CustomerCompanyUpdate,
    CustomerUserAdminCreate,
    CustomerUserAdminOut,
    CustomerUserAdminUpdate,
    CustomerUserCustomerAssignment,
    CustomerUserGroupAssignment,
    GroupOut,
)
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser, CustomerUserCustomer
from tiqora.db.legacy.user import GroupCustomerUser, PermissionGroups
from tiqora.znuny.password import hash_password

router = APIRouter(tags=["admin:customers"])


@router.get("/customer-users", response_model=Page[CustomerUserAdminOut])
async def list_customer_users(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[CustomerUserAdminOut]:
    _ = admin
    stmt = apply_valid_filter(select(CustomerUser), CustomerUser.valid_id, params.valid).order_by(
        CustomerUser.login
    )
    return await paginate(session, CustomerUserAdminOut, stmt, params)


@router.get("/customer-users/{customer_user_id}", response_model=CustomerUserAdminOut)
async def get_customer_user(
    customer_user_id: int, admin: AdminUser, session: DbSession
) -> CustomerUser:
    _ = admin
    cu = await session.get(CustomerUser, customer_user_id)
    if cu is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer user not found")
    return cu


@router.post(
    "/customer-users", response_model=CustomerUserAdminOut, status_code=status.HTTP_201_CREATED
)
async def create_customer_user(
    body: CustomerUserAdminCreate, admin: AdminUser, session: DbSession
) -> CustomerUser:
    ts = now()
    data = body.model_dump(exclude={"password"})
    cu = CustomerUser(
        **data,
        pw=hash_password(body.password) if body.password else None,
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(cu)
    await invalidate_znuny_cache_types(session, CUSTOMER_USER_CACHE_TYPES)
    await session.commit()
    await session.refresh(cu)
    return cu


@router.patch("/customer-users/{customer_user_id}", response_model=CustomerUserAdminOut)
async def update_customer_user(
    customer_user_id: int,
    body: CustomerUserAdminUpdate,
    admin: AdminUser,
    session: DbSession,
) -> CustomerUser:
    cu = await session.get(CustomerUser, customer_user_id)
    if cu is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer user not found")
    data = body.model_dump(exclude_unset=True)
    if "password" in data:
        password = data.pop("password")
        if password:
            cu.pw = hash_password(password)
    for field, value in data.items():
        setattr(cu, field, value)
    cu.change_time = now()
    cu.change_by = admin.id
    await invalidate_znuny_cache_types(session, CUSTOMER_USER_CACHE_TYPES)
    await session.commit()
    await session.refresh(cu)
    return cu


@router.delete("/customer-users/{customer_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_customer_user(
    customer_user_id: int, admin: AdminUser, session: DbSession
) -> None:
    cu = await session.get(CustomerUser, customer_user_id)
    if cu is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer user not found")
    cu.valid_id = 2
    cu.change_time = now()
    cu.change_by = admin.id
    await invalidate_znuny_cache_types(session, CUSTOMER_USER_CACHE_TYPES)
    await session.commit()


@router.get("/customer-companies", response_model=Page[CustomerCompanyOut])
async def list_customer_companies(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[CustomerCompanyOut]:
    _ = admin
    stmt = apply_valid_filter(
        select(CustomerCompany), CustomerCompany.valid_id, params.valid
    ).order_by(CustomerCompany.name)
    return await paginate(session, CustomerCompanyOut, stmt, params)


@router.get("/customer-companies/{customer_id}", response_model=CustomerCompanyOut)
async def get_customer_company(
    customer_id: str, admin: AdminUser, session: DbSession
) -> CustomerCompany:
    _ = admin
    co = await session.get(CustomerCompany, customer_id)
    if co is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return co


@router.post(
    "/customer-companies", response_model=CustomerCompanyOut, status_code=status.HTTP_201_CREATED
)
async def create_customer_company(
    body: CustomerCompanyCreate, admin: AdminUser, session: DbSession
) -> CustomerCompany:
    ts = now()
    co = CustomerCompany(
        **body.model_dump(),
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(co)
    await invalidate_znuny_cache_types(session, CUSTOMER_COMPANY_CACHE_TYPES)
    await session.commit()
    await session.refresh(co)
    return co


@router.patch("/customer-companies/{customer_id}", response_model=CustomerCompanyOut)
async def update_customer_company(
    customer_id: str,
    body: CustomerCompanyUpdate,
    admin: AdminUser,
    session: DbSession,
) -> CustomerCompany:
    co = await session.get(CustomerCompany, customer_id)
    if co is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(co, field, value)
    co.change_time = now()
    co.change_by = admin.id
    await invalidate_znuny_cache_types(session, CUSTOMER_COMPANY_CACHE_TYPES)
    await session.commit()
    await session.refresh(co)
    return co


@router.delete("/customer-companies/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_customer_company(
    customer_id: str, admin: AdminUser, session: DbSession
) -> None:
    co = await session.get(CustomerCompany, customer_id)
    if co is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    co.valid_id = 2
    co.change_time = now()
    co.change_by = admin.id
    await invalidate_znuny_cache_types(session, CUSTOMER_COMPANY_CACHE_TYPES)
    await session.commit()


@router.get(
    "/customer-companies/{customer_id}/customer-users",
    response_model=list[CustomerUserAdminOut],
)
async def get_customer_company_users(
    customer_id: str, admin: AdminUser, session: DbSession
) -> list[CustomerUser]:
    """Customer users with extra visibility into *customer_id* — reverse of
    customer-user↔companies (Znuny ``customer_user_customer``).

    Distinct from users whose *primary* ``customer_user.customer_id`` is this
    company; those are not listed here.
    """
    _ = admin
    co = await session.get(CustomerCompany, customer_id)
    if co is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    result = await session.execute(
        select(CustomerUser)
        .join(CustomerUserCustomer, CustomerUserCustomer.user_id == CustomerUser.login)
        .where(CustomerUserCustomer.customer_id == customer_id)
        .order_by(CustomerUser.login)
    )
    return list(result.scalars().all())


@router.get(
    "/customer-users/{customer_user_login}/companies", response_model=list[CustomerCompanyOut]
)
async def get_customer_user_companies(
    customer_user_login: str, admin: AdminUser, session: DbSession
) -> list[CustomerCompany]:
    """Companies *customer_user_login* has additional ticket visibility into —
    the Customer-User↔Customers editor's read side.

    This is the Znuny ``customer_user_customer`` M2M (extra visibility), keyed
    by the customer-user *login*. It is distinct from the user's single primary
    ``customer_user.customer_id`` (their home company), which is edited on the
    Customer Users page instead."""
    _ = admin
    result = await session.execute(
        select(CustomerCompany)
        .join(CustomerUserCustomer, CustomerUserCustomer.customer_id == CustomerCompany.customer_id)
        .where(CustomerUserCustomer.user_id == customer_user_login)
    )
    return list(result.scalars().all())


@router.put(
    "/customer-users/{customer_user_login}/companies", status_code=status.HTTP_204_NO_CONTENT
)
async def assign_customer_company(
    customer_user_login: str,
    body: CustomerUserCustomerAssignment,
    admin: AdminUser,
    session: DbSession,
) -> None:
    """Grant *customer_user_login* additional visibility into *customer_id*'s
    tickets (Znuny ``customer_user_customer`` — distinct from the user's
    primary ``customer_user.customer_id``)."""
    existing = await session.get(CustomerUserCustomer, (customer_user_login, body.customer_id))
    ts = now()
    if existing is None:
        session.add(
            CustomerUserCustomer(
                user_id=customer_user_login,
                customer_id=body.customer_id,
                create_time=ts,
                create_by=admin.id,
                change_time=ts,
                change_by=admin.id,
            )
        )
        await invalidate_znuny_cache_types(session, CUSTOMER_USER_CACHE_TYPES)
        await session.commit()


@router.delete(
    "/customer-users/{customer_user_login}/companies/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_customer_company(
    customer_user_login: str, customer_id: str, admin: AdminUser, session: DbSession
) -> None:
    _ = admin
    existing = await session.get(CustomerUserCustomer, (customer_user_login, customer_id))
    if existing is not None:
        await session.delete(existing)
        await invalidate_znuny_cache_types(session, CUSTOMER_USER_CACHE_TYPES)
        await session.commit()


# --- Customer-user ↔ Groups (group_customer_user, keyed by login) ------------


@router.get("/customer-users/{login}/groups", response_model=list[GroupOut])
async def get_customer_user_groups(
    login: str, admin: AdminUser, session: DbSession
) -> list[PermissionGroups]:
    """Groups the customer user has full (``rw``) access to — the
    Customer-User↔Groups editor's read side.

    Znuny stores the customer-user identity as the *login string* in
    ``group_customer_user.user_id`` (not the numeric ``customer_user.id``).
    The editor toggles ``rw`` (see :func:`assign_customer_user_group`), so the
    read set is filtered to that key — same pattern as agent↔group.
    """
    _ = admin
    result = await session.execute(
        select(PermissionGroups)
        .join(GroupCustomerUser, GroupCustomerUser.group_id == PermissionGroups.id)
        .where(
            GroupCustomerUser.user_id == login,
            GroupCustomerUser.permission_key == "rw",
            GroupCustomerUser.permission_value == 1,
        )
        .order_by(PermissionGroups.name)
    )
    return list(result.scalars().all())


@router.put("/customer-users/{login}/groups", status_code=status.HTTP_204_NO_CONTENT)
async def assign_customer_user_group(
    login: str,
    body: CustomerUserGroupAssignment,
    admin: AdminUser,
    session: DbSession,
) -> None:
    """Grant *login* the given permission on *group_id*.

    Upserts into Znuny ``group_customer_user`` (composite identity:
    login + group_id + permission_key). ``permission_value`` is required by
    the table (unlike agent ``group_user``).
    """
    # Ensure the customer user exists (lookup by login, not numeric id).
    result = await session.execute(select(CustomerUser).where(CustomerUser.login == login))
    cu = result.scalar_one_or_none()
    if cu is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer user not found")

    existing = await session.get(GroupCustomerUser, (login, body.group_id, body.permission_key))
    ts = now()
    if existing is None:
        session.add(
            GroupCustomerUser(
                user_id=login,
                group_id=body.group_id,
                permission_key=body.permission_key,
                permission_value=body.permission_value,
                create_time=ts,
                create_by=admin.id,
                change_time=ts,
                change_by=admin.id,
            )
        )
    else:
        existing.permission_value = body.permission_value
        existing.change_time = ts
        existing.change_by = admin.id
    await invalidate_znuny_cache_types(session, CUSTOMER_USER_GROUP_CACHE_TYPES)
    await session.commit()


@router.delete(
    "/customer-users/{login}/groups/{group_id}/{permission_key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_customer_user_group(
    login: str,
    group_id: int,
    permission_key: str,
    admin: AdminUser,
    session: DbSession,
) -> None:
    _ = admin
    existing = await session.get(GroupCustomerUser, (login, group_id, permission_key))
    if existing is not None:
        await session.delete(existing)
        await invalidate_znuny_cache_types(session, CUSTOMER_USER_GROUP_CACHE_TYPES)
        await session.commit()
