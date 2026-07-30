"""DESIGN.md section 8.3: reconciliation as a trust boundary."""

from datetime import date

import pytest

from ledger import (
    RTA_INFLOW,
    AccountType,
    ClearedStatus,
    LedgerError,
    Plan,
    YMonth,
    money,
)

JUL = YMonth(2026, 7)
RECONCILE_DAY = date(2026, 7, 10)


def _plan_with_history() -> tuple[Plan, str, str]:
    """Checking with two cleared transactions and one uncleared; returns
    (plan, cleared txn id, uncleared txn id)."""
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("g", "Group")
    plan.add_category("misc", "Misc", "g")
    plan.add_transaction(
        "checking",
        date(2026, 7, 1),
        500,
        category_id=RTA_INFLOW,
        payee="Employer",
        cleared=True,
    )
    spent = plan.add_transaction(
        "checking",
        date(2026, 7, 5),
        -120,
        category_id="misc",
        payee="Store",
        cleared=True,
    )
    pending = plan.add_transaction(
        "checking", date(2026, 7, 9), -80, category_id="misc", payee="Pending Store"
    )
    return plan, spent.id, pending.id


def test_cleared_balance_counts_only_cleared_transactions() -> None:
    plan, _, pending_id = _plan_with_history()
    assert plan.cleared_balance("checking") == money(380)
    assert plan.account_balance("checking") == money(300)
    plan.set_cleared(pending_id, cleared=True)
    assert plan.cleared_balance("checking") == money(300)


def test_matching_reconciliation_locks_cleared_history_without_adjustment() -> None:
    plan, spent_id, pending_id = _plan_with_history()
    adjustment = plan.reconcile("checking", RECONCILE_DAY, 380)
    assert adjustment is None
    assert plan.get_transaction(spent_id).status is ClearedStatus.RECONCILED
    assert plan.get_transaction(pending_id).status is ClearedStatus.UNCLEARED
    assert plan.accounts["checking"].last_reconciled == RECONCILE_DAY


def test_locked_transactions_refuse_status_changes_and_deletion() -> None:
    plan, spent_id, pending_id = _plan_with_history()
    plan.reconcile("checking", RECONCILE_DAY, 380)
    with pytest.raises(LedgerError):
        plan.set_cleared(spent_id, cleared=False)
    with pytest.raises(LedgerError):
        plan.delete_transaction(spent_id)
    plan.delete_transaction(pending_id)  # unlocked transactions still delete
    assert plan.account_balance("checking") == money(380)


def test_mismatch_inserts_a_locked_adjustment_through_rta() -> None:
    plan, _, _ = _plan_with_history()
    rta_before = plan.rta(JUL)
    adjustment = plan.reconcile("checking", RECONCILE_DAY, 350)
    assert adjustment is not None
    assert adjustment.amount == money(-30)
    assert adjustment.category_id == RTA_INFLOW
    assert plan.get_transaction(adjustment.id).status is ClearedStatus.RECONCILED
    assert plan.cleared_balance("checking") == money(350)
    assert plan.rta(JUL) == rta_before - money(30)


def test_credit_account_adjustment_is_uncategorized() -> None:
    plan, _, _ = _plan_with_history()
    plan.add_account("card", "Card", AccountType.CREDIT_CARD)
    plan.assign("misc", JUL, 50)
    plan.add_transaction(
        "card", date(2026, 7, 6), -30, category_id="misc", payee="Cafe", cleared=True
    )
    rta_before = plan.rta(JUL)
    adjustment = plan.reconcile("card", RECONCILE_DAY, -25)
    assert adjustment is not None
    assert adjustment.amount == money(5)
    assert adjustment.category_id is None
    assert plan.rta(JUL) == rta_before
    assert plan.cleared_balance("card") == money(-25)


def test_deleting_one_transfer_leg_deletes_both() -> None:
    plan, _, _ = _plan_with_history()
    plan.add_account("savings", "Savings", AccountType.SAVINGS)
    out_leg, in_leg = plan.add_transfer("checking", "savings", date(2026, 7, 7), 100)
    plan.delete_transaction(out_leg.id)
    assert plan.account_balance("savings") == money(0)
    assert plan.account_balance("checking") == money(300)
    with pytest.raises(LedgerError):
        plan.get_transaction(in_leg.id)
