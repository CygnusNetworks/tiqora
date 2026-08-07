"""Length policy for passwords this product sets itself."""

from __future__ import annotations

import pytest

from tiqora.api.v1.admin.schemas import CustomerUserAdminCreate, UserCreate, UserUpdate
from tiqora.domain.password_policy import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    validate_password,
)


def test_bounds_are_the_nist_shaped_ones() -> None:
    assert MIN_PASSWORD_LENGTH == 12
    assert MAX_PASSWORD_LENGTH == 64


@pytest.mark.parametrize("length", [MIN_PASSWORD_LENGTH, 30, MAX_PASSWORD_LENGTH])
def test_accepts_the_whole_permitted_range(length: int) -> None:
    validate_password("x" * length)


@pytest.mark.parametrize("length", [0, 1, MIN_PASSWORD_LENGTH - 1])
def test_rejects_too_short(length: int) -> None:
    with pytest.raises(PasswordPolicyError, match="at least"):
        validate_password("x" * length)


def test_rejects_too_long() -> None:
    with pytest.raises(PasswordPolicyError, match="at most"):
        validate_password("x" * (MAX_PASSWORD_LENGTH + 1))


def test_length_is_counted_in_characters_not_bytes() -> None:
    """An umlaut costs two UTF-8 bytes but is one character to the person
    typing it — the bound they are told about is the one enforced."""
    validate_password("ä" * MAX_PASSWORD_LENGTH)


@pytest.mark.parametrize(
    "model",
    [UserCreate, UserUpdate, CustomerUserAdminCreate],
)
def test_admin_schemas_reject_out_of_range_passwords(model: type) -> None:
    """The bound lives on the field so it reaches OpenAPI, not just the
    handler — check it actually bites on every admin write model."""
    base: dict[str, object] = {
        "login": "someone",
        "first_name": "Some",
        "last_name": "One",
        "email": "someone@example.com",
        "customer_id": "acme",
    }
    with pytest.raises(ValueError, match="at least 12"):
        model(**base, password="x" * (MIN_PASSWORD_LENGTH - 1))
    with pytest.raises(ValueError, match="at most 64"):
        model(**base, password="x" * (MAX_PASSWORD_LENGTH + 1))
    # And the permitted range still constructs.
    model(**base, password="x" * MIN_PASSWORD_LENGTH)
