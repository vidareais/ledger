"""DESIGN.md section 5.2: transfers as double-entry, category-neutral moves."""

from datetime import date

import pytest

from ledger import RTA_INFLOW, AccountType, LedgerError, Plan, YMonth, money

JUL = YMonth(2026, 7)


def _plan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_account("savings", "Savings", AccountType.SAVINGS)
    plan.add_account("invest", "Investment", AccountType.INVESTMENT)
    plan.add_category_group("g", "Group")
    plan.add_category("gifts", "Gifts", "g")
    plan.add_transaction(
        "checking", date(2026, 7, 1), 500, category_id=RTA_INFLOW, payee="Employer"
    )
    return plan


def test_budget_transfer_is_a_linked_pair_with_structural_payees() -> None:
    plan = _plan()
    rta_before = plan.rta(JUL)
    out_leg, in_leg = plan.add_transfer("checking", "savings", date(2026, 7, 5), 100)
    assert plan.account_balance("checking") == money(400)
    assert plan.account_balance("savings") == money(100)
    assert plan.rta(JUL) == rta_before
    assert out_leg.transfer_id == in_leg.transfer_id
    assert out_leg.payee_id is not None and in_leg.payee_id is not None
    assert plan.payees[out_leg.payee_id].name == "Transfer: Savings"
    assert plan.payees[in_leg.payee_id].name == "Transfer: Checking"
    assert plan.payees[out_leg.payee_id].structural


def test_budget_transfer_rejects_a_category() -> None:
    plan = _plan()
    with pytest.raises(LedgerError):
        plan.add_transfer(
            "checking", "savings", date(2026, 7, 5), 100, category_id="gifts"
        )


def test_transfer_to_tracking_requires_a_category_and_is_spending() -> None:
    plan = _plan()
    with pytest.raises(LedgerError):
        plan.add_transfer("checking", "invest", date(2026, 7, 5), 100)
    plan.assign("gifts", JUL, 150)
    plan.add_transfer("checking", "invest", date(2026, 7, 5), 100, category_id="gifts")
    assert plan.activity("gifts", JUL) == money(-100)
    assert plan.available("gifts", JUL) == money(50)
    assert plan.account_balance("invest") == money(100)


def test_transfer_from_tracking_can_fund_rta() -> None:
    plan = _plan()
    plan.add_transfer("checking", "invest", date(2026, 7, 5), 100, category_id="gifts")
    rta_before = plan.rta(JUL)
    plan.add_transfer(
        "invest", "checking", date(2026, 7, 10), 40, category_id=RTA_INFLOW
    )
    assert plan.rta(JUL) == rta_before + money(40)
    assert plan.account_balance("invest") == money(60)
