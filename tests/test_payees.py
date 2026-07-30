"""DESIGN.md section 8.4: payee referential integrity and merges."""

from datetime import date

import pytest

from ledger import (
    RTA_INFLOW,
    AccountType,
    ClearedStatus,
    Frequency,
    LedgerError,
    Plan,
    UnknownEntityError,
    money,
)


def _plan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("wants", "Wants")
    plan.add_category("dining", "Dining Out", "wants")
    plan.add_transaction(
        "checking", date(2026, 7, 1), 500, category_id=RTA_INFLOW, payee="Employer"
    )
    plan.add_transaction(
        "checking", date(2026, 7, 5), -50, category_id="dining", payee="Restaurant"
    )
    plan.add_transaction(
        "checking", date(2026, 7, 8), -30, category_id="dining", payee="Uber"
    )
    return plan


def _payee_id(plan: Plan, name: str) -> str:
    return next(p.id for p in plan.payees.values() if p.name == name)


def test_merge_repoints_history_and_retires_the_losing_payee() -> None:
    plan = _plan()
    repointed = plan.merge_payees(
        _payee_id(plan, "Uber"), _payee_id(plan, "Restaurant")
    )
    assert repointed == 1
    assert all(p.name != "Uber" for p in plan.payees.values())
    surviving = _payee_id(plan, "Restaurant")
    amounts = sorted(t.amount for t in plan.transactions() if t.payee_id == surviving)
    assert amounts == [money(-50), money(-30)]


def test_merge_repoints_schedules_too() -> None:
    plan = _plan()
    schedule, _ = plan.add_schedule(
        "checking",
        date(2026, 8, 1),
        -30,
        Frequency.MONTHLY,
        category_id="dining",
        payee="Uber",
    )
    repointed = plan.merge_payees(
        _payee_id(plan, "Uber"), _payee_id(plan, "Restaurant")
    )
    assert repointed == 2  # one transaction, one schedule
    assert schedule.payee_id == _payee_id(plan, "Restaurant")


def test_merge_rewrites_even_reconciled_rows() -> None:
    plan = _plan()
    uber_txn = next(
        t for t in plan.transactions() if t.payee_id == _payee_id(plan, "Uber")
    )
    plan.set_cleared(uber_txn.id, cleared=True)
    plan.reconcile("checking", date(2026, 7, 10), plan.cleared_balance("checking"))
    plan.merge_payees(_payee_id(plan, "Uber"), _payee_id(plan, "Restaurant"))
    merged = plan.get_transaction(uber_txn.id)
    assert merged.payee_id == _payee_id(plan, "Restaurant")
    assert merged.status is ClearedStatus.RECONCILED  # lock survives the rewrite


def test_structural_payees_refuse_merge_and_rename() -> None:
    plan = _plan()
    plan.add_account("savings", "Savings", AccountType.SAVINGS)
    plan.add_transfer("checking", "savings", date(2026, 7, 9), 100)
    transfer_payee = _payee_id(plan, "Transfer: Savings")
    with pytest.raises(LedgerError):
        plan.merge_payees(transfer_payee, _payee_id(plan, "Restaurant"))
    with pytest.raises(LedgerError):
        plan.merge_payees(_payee_id(plan, "Restaurant"), transfer_payee)
    with pytest.raises(LedgerError):
        plan.rename_payee(transfer_payee, "My Transfers")


def test_rename_changes_ordinary_payees() -> None:
    plan = _plan()
    renamed = plan.rename_payee(_payee_id(plan, "Uber"), "Rideshare")
    assert renamed.name == "Rideshare"
    assert all(p.name != "Uber" for p in plan.payees.values())


def test_merge_rejects_self_and_unknown_ids() -> None:
    plan = _plan()
    restaurant = _payee_id(plan, "Restaurant")
    with pytest.raises(LedgerError):
        plan.merge_payees(restaurant, restaurant)
    with pytest.raises(UnknownEntityError):
        plan.merge_payees("payee-999", restaurant)
