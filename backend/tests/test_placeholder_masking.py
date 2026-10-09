"""Znuny ``_MaskSensitiveValue`` parity for placeholder rendering (pure, no DB)."""

from __future__ import annotations

import pytest

from tiqora.channels.email.placeholder import PlaceholderContext, expand_placeholders


class _Cfg:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


async def _render(text: str, cfg: dict[str, str] | None = None, **ctx_kw: object) -> str:
    ctx = PlaceholderContext(**ctx_kw)  # type: ignore[arg-type]
    return await expand_placeholders(None, _Cfg(cfg or {}), text, context=ctx)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_customer_secret_fields_masked_normal_fields_not() -> None:
    cust = {
        "userpassword": "$2y$hash",
        "pw": "hash2",
        "userfirstname": "Ada",
        "useremail": "ada@example.org",
    }
    out = await _render(
        "<OTRS_CUSTOMER_DATA_UserPassword>|<OTRS_CUSTOMER_pw>|"
        "<OTRS_CUSTOMER_DATA_UserFirstname>|<OTRS_CUSTOMER_DATA_UserEmail>",
        customer=cust,
    )
    assert out == "xxx|xxx|Ada|ada@example.org"


@pytest.mark.asyncio
async def test_current_user_pw_masked() -> None:
    out = await _render(
        "<OTRS_CURRENT_UserPw>|<OTRS_CURRENT_UserFirstname>",
        current_user={"userpw": "secret-hash", "userfirstname": "Bob"},
    )
    assert out == "xxx|Bob"


@pytest.mark.asyncio
async def test_config_password_masked_other_config_not() -> None:
    cfg = {
        "SendmailModule::AuthPassword": "hunter2",
        "Database::Pw1": "dbpass",
        "FQDN": "tickets.example.org",
    }
    out = await _render(
        "<OTRS_CONFIG_SendmailModule::AuthPassword>|<OTRS_CONFIG_SendmailModule_AuthPassword>|"
        "<OTRS_CONFIG_Database_Pw1>|<OTRS_CONFIG_FQDN>",
        cfg,
    )
    assert out == "xxx|xxx|xxx|tickets.example.org"


@pytest.mark.asyncio
async def test_config_override_password_masked() -> None:
    out = await _render(
        "<OTRS_CONFIG_Some_Password>|<OTRS_CONFIG_Host>",
        config_overrides={"some::password": "leak", "host": "h.example.org"},
    )
    assert out == "xxx|h.example.org"


@pytest.mark.asyncio
async def test_dynamic_field_tags_not_masked() -> None:
    out = await _render("<OTRS_TICKET_DynamicField_Token>", ticket={"dynamicfield_token": "abc"})
    assert out == "abc"
