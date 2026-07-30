"""Editing transactions in place with update_transaction."""

from datetime import date

import pytest

from ledger import (
    RTA_INFLOW,
    ZERO,
    AccountType,
    LedgerError,
    Plan,
    SplitLine,
    SplitMismatchError,
    YMonth,
    money,
)

JUL = YMonth(2026, 7)
AUG = YMonth(2026, 8)


def _plan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_account("savings", "Savings", AccountType.SAVINGS)
    plan.add_account("invest", "Investment", AccountType.INVESTMENT)
    plan.add_category_group("g", "Group")
    plan.add_category("dining", "Dining Out", "g")
    plan.add_category("fun", "Fun", "g")
    plan.add_transaction(
        "checking", date(2026, 7, 1), 500, category_id=RTA_INFLOW, payee="Employer"
    )
    return plan


def test_plain_edits_update_derived_figures() -> None:
    plan = _plan()
    txn = plan.add_transaction(
        "checking", date(2026, 7, 5), -40, category_id="dining", payee="Cafe"
    )
    updated = plan.update_transaction(
        txn.id, amount=-60, category_id="fun", payee="Bistro", memo="team lunch"
    )
    assert updated.amount == money(-60)
    assert plan.activity("dining", JUL) == ZERO
    assert plan.activity("fun", JUL) == money(-60)
    assert plan.account_balance("checking") == money(440)
    assert updated.payee_id is not None
    assert plan.payees[updated.payee_id].name == "Bistro"
    assert updated.memo == "team lunch"


def test_moving_the_date_moves_monthly_activity() -> None:
    plan = _plan()
    txn = plan.add_transaction(
        "checking", date(2026, 7, 5), -40, category_id="dining", payee="Cafe"
    )
    plan.update_transaction(txn.id, when=date(2026, 8, 5))
    assert plan.activity("dining", JUL) == ZERO
    assert plan.activity("dining", AUG) == money(-40)


def test_reconciled_transactions_are_locked() -> None:
    plan = _plan()
    txn = plan.add_transaction(
        "checking",
        date(2026, 7, 5),
        -40,
        category_id="dining",
        payee="Cafe",
        cleared=True,
    )
    plan.reconcile("checking", date(2026, 7, 6), plan.cleared_balance("checking"))
    with pytest.raises(LedgerError):
        plan.update_transaction(txn.id, amount=-45)


def test_splitting_an_existing_transaction() -> None:
    plan = _plan()
    txn = plan.add_transaction(
        "checking",
        date(2026, 7, 5),
        money("-52.50"),
        category_id="dining",
        payee="Target",
    )
    plan.update_transaction(
        txn.id,
        category_id=None,
        splits=[SplitLine("dining", money(-30)), SplitLine("fun", money("-22.50"))],
    )
    assert plan.activity("dining", JUL) == money(-30)
    assert plan.activity("fun", JUL) == money("-22.50")


def test_invalid_edits_change_nothing() -> None:
    plan = _plan()
    txn = plan.add_transaction(
        "checking",
        date(2026, 7, 5),
        money("-52.50"),
        category_id="dining",
        payee="Target",
    )
    with pytest.raises(SplitMismatchError):
        plan.update_transaction(
            txn.id,
            category_id=None,
            splits=[SplitLine("dining", money(-30)), SplitLine("fun", money(-20))],
        )
    with pytest.raises(LedgerError):  # category was not cleared alongside the splits
        plan.update_transaction(txn.id, splits=[SplitLine("dining", money("-52.50"))])
    with pytest.raises(LedgerError):  # budget accounts must stay categorized
        plan.update_transaction(txn.id, category_id=None)
    assert plan.get_transaction(txn.id) == txn


def test_transfer_amount_and_date_edits_mirror_to_the_linked_leg() -> None:
    plan = _plan()
    out_leg, in_leg = plan.add_transfer("checking", "savings", date(2026, 7, 6), 100)
    plan.update_transaction(
        out_leg.id, amount=-150, when=date(2026, 7, 7), memo="rainy day"
    )
    updated_in = plan.get_transaction(in_leg.id)
    assert plan.account_balance("savings") == money(150)
    assert plan.account_balance("checking") == money(350)
    assert updated_in.amount == money(150)
    assert updated_in.date == date(2026, 7, 7)
    assert updated_in.memo == ""  # memo stays leg-local


def test_transfer_edit_guards() -> None:
    plan = _plan()
    out_leg, in_leg = plan.add_transfer("checking", "savings", date(2026, 7, 6), 100)
    with pytest.raises(LedgerError):
        plan.update_transaction(out_leg.id, payee="Someone")
    with pytest.raises(LedgerError):
        plan.update_transaction(out_leg.id, splits=[SplitLine("dining", money(-100))])
    with pytest.raises(LedgerError):  # direction flip
        plan.update_transaction(out_leg.id, amount=100)
    with pytest.raises(LedgerError):  # budget<->budget transfers take no category
        plan.update_transaction(out_leg.id, category_id="dining")
    plan.set_cleared(in_leg.id, cleared=True)
    plan.reconcile("savings", date(2026, 7, 8), plan.cleared_balance("savings"))
    with pytest.raises(LedgerError):  # counterpart is locked
        plan.update_transaction(out_leg.id, amount=-120)
    updated = plan.update_transaction(out_leg.id, memo="ok")  # leg-local still fine
    assert updated.memo == "ok"


def test_tracking_transfer_category_can_change_but_not_clear() -> None:
    plan = _plan()
    out_leg, _ = plan.add_transfer(
        "checking", "invest", date(2026, 7, 6), 100, category_id="dining"
    )
    plan.update_transaction(out_leg.id, category_id="fun")
    assert plan.activity("dining", JUL) == ZERO
    assert plan.activity("fun", JUL) == money(-100)
    with pytest.raises(LedgerError):
        plan.update_transaction(out_leg.id, category_id=None)
