"""DESIGN.md section 7.1: display order is the only Auto-Assign priority."""

from datetime import date

import pytest

from ledger import (
    PAYMENT_GROUP_ID,
    RTA_INFLOW,
    AccountType,
    LedgerError,
    MonthlyTarget,
    Plan,
    UnknownEntityError,
    YMonth,
    money,
)

JUL = YMonth(2026, 7)
TODAY = date(2026, 7, 15)


def _plan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("bills", "Bills")
    plan.add_category_group("wants", "Wants")
    plan.add_category("phone", "Phone", "bills")
    plan.add_category("internet", "Internet", "bills")
    plan.add_category("vacation", "Vacation", "wants")
    plan.set_target("phone", MonthlyTarget(money(80)))
    plan.set_target("internet", MonthlyTarget(money(50)))
    plan.set_target("vacation", MonthlyTarget(money(200)))
    plan.add_transaction(
        "checking", date(2026, 7, 1), 100, category_id=RTA_INFLOW, payee="Employer"
    )
    return plan


def test_reordering_within_a_group_changes_funding_priority() -> None:
    plan = _plan()
    assert plan.preview_underfunded(JUL, TODAY) == {
        "phone": money(80),
        "internet": money(20),
    }
    plan.move_category("internet", "bills", 0)
    assert plan.preview_underfunded(JUL, TODAY) == {
        "internet": money(50),
        "phone": money(50),
    }


def test_moving_between_groups_repositions_in_display_order() -> None:
    plan = _plan()
    plan.move_category("vacation", "bills", 0)
    assert plan.display_order() == ["vacation", "phone", "internet"]
    assert plan.categories["vacation"].group_id == "bills"
    assert plan.groups["wants"].category_ids == []
    assert plan.preview_underfunded(JUL, TODAY) == {"vacation": money(100)}


def test_position_defaults_to_append_and_clamps_past_the_end() -> None:
    plan = _plan()
    plan.move_category("phone", "wants")
    assert plan.groups["wants"].category_ids == ["vacation", "phone"]
    plan.move_category("internet", "wants", 99)
    assert plan.groups["wants"].category_ids == ["vacation", "phone", "internet"]
    with pytest.raises(LedgerError):
        plan.move_category("internet", "wants", -1)


def test_payment_categories_are_structural() -> None:
    plan = _plan()
    plan.add_account("card", "Card", AccountType.CREDIT_CARD)
    plan.add_account("loc", "LOC", AccountType.LINE_OF_CREDIT)
    with pytest.raises(LedgerError):
        plan.move_category("payment:card", "bills")
    with pytest.raises(LedgerError):
        plan.move_category("phone", PAYMENT_GROUP_ID)
    plan.move_category("payment:loc", PAYMENT_GROUP_ID, 0)  # reorder inside is fine
    assert plan.groups[PAYMENT_GROUP_ID].category_ids == [
        "payment:loc",
        "payment:card",
    ]


def test_move_group_repositions_the_whole_group() -> None:
    plan = _plan()
    plan.move_group("wants", 0)
    assert plan.display_order() == ["vacation", "phone", "internet"]
    assert plan.preview_underfunded(JUL, TODAY) == {"vacation": money(100)}
    with pytest.raises(LedgerError):
        plan.move_group("wants", -1)
    with pytest.raises(UnknownEntityError):
        plan.move_group("missing", 0)
    with pytest.raises(UnknownEntityError):
        plan.move_category("phone", "missing")


def test_new_order_survives_serialization() -> None:
    plan = _plan()
    plan.move_group("wants", 0)
    plan.move_category("internet", "bills", 0)
    reloaded = Plan.from_dict(plan.to_dict())
    assert reloaded.display_order() == plan.display_order()
    assert reloaded.categories["internet"].group_id == "bills"
