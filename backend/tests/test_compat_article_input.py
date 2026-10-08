"""Pure helpers of the GenericInterface TicketCreate / TicketUpdate input (no database)."""

from __future__ import annotations

from tiqora.api.compat.article_input import (
    article_id_result,
    normalize_article_list,
    split_dynamic_fields,
)

NO_ARTICLE_FIELDS: frozenset[str] = frozenset()


def test_split_scalar_list_and_none_values() -> None:
    items = [
        {"Name": "Text", "Value": "x"},
        {"Name": "Multi", "Value": ["a", "b"]},
        {"Name": "Cleared", "Value": None},
        {"Name": "Number", "Value": 5},
    ]
    ticket, article = split_dynamic_fields(items, NO_ARTICLE_FIELDS)
    assert ticket == {"Text": ["x"], "Multi": ["a", "b"], "Cleared": [], "Number": ["5"]}
    assert article == {}


def test_split_list_value_is_not_stringified_as_a_list() -> None:
    ticket, _ = split_dynamic_fields([{"Name": "Multi", "Value": ["a", "b"]}], NO_ARTICLE_FIELDS)
    assert ticket["Multi"] != ["['a', 'b']"]
    assert ticket["Multi"] == ["a", "b"]


def test_split_by_article_field_names_keeps_order() -> None:
    items = [
        {"Name": "T1", "Value": "1"},
        {"Name": "A1", "Value": ["a", "b"]},
        {"Name": "T2", "Value": "2"},
    ]
    ticket, article = split_dynamic_fields(items, frozenset({"A1"}))
    assert list(ticket) == ["T1", "T2"]
    assert article == {"A1": ["a", "b"]}


def test_split_accepts_a_single_hash_instead_of_a_list() -> None:
    ticket, article = split_dynamic_fields({"Name": " Text ", "Value": " x "}, NO_ARTICLE_FIELDS)
    # Znuny trims the scalar values of every item
    assert ticket == {"Text": ["x"]}
    assert article == {}


def test_split_ignores_junk_items() -> None:
    items = ["nope", {"Value": "x"}, {"Name": "", "Value": "x"}, None, {"Name": "Ok", "Value": "1"}]
    ticket, article = split_dynamic_fields(items, NO_ARTICLE_FIELDS)
    assert ticket == {"Ok": ["1"]}
    assert article == {}
    assert split_dynamic_fields("garbage", NO_ARTICLE_FIELDS) == ({}, {})


def test_normalize_article_list_shapes() -> None:
    one = {"Subject": "a"}
    two = {"Subject": "b"}
    assert normalize_article_list(one) == [one]
    assert normalize_article_list([one, "junk", two, None]) == [one, two]
    assert normalize_article_list({}) == []
    assert normalize_article_list(None) == []
    assert normalize_article_list("x") == []


def test_create_returns_bare_id_for_one_article_and_a_list_for_several() -> None:
    base = {"TicketID": 1, "TicketNumber": "N"}
    assert article_id_result(base, [5], op="TicketCreate") == {**base, "ArticleID": 5}
    assert article_id_result(base, [5, 6], op="TicketCreate") == {**base, "ArticleID": [5, 6]}
    assert article_id_result(base, [], op="TicketCreate") == base


def test_update_returns_article_id_or_article_ids_or_nothing() -> None:
    base = {"TicketID": 1, "TicketNumber": "N"}
    assert article_id_result(base, [5], op="TicketUpdate") == {**base, "ArticleID": 5}
    assert article_id_result(base, [5, 6], op="TicketUpdate") == {**base, "ArticleIDs": [5, 6]}
    assert article_id_result(base, [], op="TicketUpdate") == base


def test_article_id_result_does_not_mutate_its_input() -> None:
    base = {"TicketID": 1}
    article_id_result(base, [5, 6], op="TicketUpdate")
    assert base == {"TicketID": 1}


def test_sysconfig_defaults_of_the_gi_article_settings() -> None:
    from tiqora.znuny.sysconfig import ZNUNY_SETTING_DEFAULTS as D

    create = "GenericInterface::Operation::TicketCreate###"
    update = "GenericInterface::Operation::TicketUpdate###"
    assert D[create + "IsVisibleForCustomer"] == 1
    assert D[create + "HistoryType"] == "NewTicket"
    assert D[create + "HistoryComment"] == "%%GenericInterface Create"
    assert D[create + "AutoResponseType"] == "auto reply"
    assert D[update + "IsVisibleForCustomer"] == 1
    assert D[update + "HistoryType"] == "AddNote"
    assert D[update + "HistoryComment"] == "%%GenericInterface Note"
    assert D[update + "AutoResponseType"] == "auto follow up"
    assert D["Ticket::Frontend::AccountTime"] == 1
    assert D["Ticket::Frontend::NeedAccountedTime"] == 0


def test_plain_signature_behind_an_html_body_is_escaped_with_line_breaks() -> None:
    from tiqora.channels.email.outbound_reply import _signature_for_body

    html_body = "text/html; charset=utf-8"
    assert (
        _signature_for_body("Team <b>&\nAmt", "text/plain", html_body)
        == "Team &lt;b&gt;&amp;<br/>\nAmt"
    )
    assert _signature_for_body("Team", None, html_body) == "Team"


def test_html_signature_behind_a_plain_body_becomes_text() -> None:
    from tiqora.channels.email.outbound_reply import _signature_for_body

    out = _signature_for_body("<p>Team <b>Amt</b></p>", "text/html", "text/plain; charset=utf-8")
    assert "<" not in out and "Team" in out and "Amt" in out


def test_matching_signature_and_body_formats_are_left_alone() -> None:
    from tiqora.channels.email.outbound_reply import _signature_for_body

    assert _signature_for_body("<i>x</i>", "text/html", "text/html") == "<i>x</i>"
    assert _signature_for_body("a\nb", "text/plain", "text/plain") == "a\nb"
