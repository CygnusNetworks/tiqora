"""same_value / differing_fields: what the vCard import calls a conflict.

All names and numbers are invented.
"""

from __future__ import annotations

import pytest

from tiqora.db.legacy.customer import CustomerUser
from tiqora.domain.customer_fill import differing_fields, same_value


@pytest.mark.parametrize(
    ("name", "current", "incoming", "same"),
    [
        ("last_name", "Beispiel", "beispiel ", True),
        ("first_name", "Anna  Maria", "Anna Maria", True),
        ("last_name", "Beispiel", "Muster", False),
        ("email", "Anna@Example.org", "anna@example.org", True),
        ("phone", "0228 5550121", "+49 228 5550121", True),
        ("phone", "0049 228 5550121", "+49 (228) 555-0121", True),
        ("mobile", "+49 170 1234567", "+49 171 1234567", False),
        # Too short to tell a trunk prefix from a different number.
        ("phone", "123", "49123", False),
    ],
)
def test_same_value(name: str, current: str, incoming: str, same: bool) -> None:
    assert same_value(name, current, incoming) is same


def test_differing_fields_skips_blanks() -> None:
    cu = CustomerUser(
        login="x",
        email="x@example.org",
        first_name="",
        last_name="Alt",
        phone="0228 1",
        mobile=None,
    )
    values = {
        "first_name": "Neu",  # blank on the customer: a fill, not a conflict
        "last_name": "Neu",
        "email": "x@example.org",
        "phone": "0228 2",
        "mobile": "0170 3",
    }
    assert differing_fields(cu, values) == ["last_name", "phone"]
