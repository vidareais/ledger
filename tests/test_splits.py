"""DESIGN.md sections 8.1 and 11 vector 9: split-transaction invariant."""

from datetime import date

import pytest

from ledger import (
    RTA_INFLOW,
    AccountType,
    Plan,
    SplitLine,
    SplitMismatchError,
    YMonth,
    money,
)

JUL = YMonth(2026, 7)


def _plan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("needs", "Needs")
    plan.add_category("groceries", "Groceries", "needs")
    plan.add_category("household", "Household", "needs")
    plan.add_transaction(
        "checking", date(2026, 7, 1), 200, category_id=RTA_INFLOW, payee="Employer"
    )
    return plan


def test_split_is_one_row_affecting_the_balance_once() -> None:
    plan = _plan()
    txn = plan.add_transaction(
        "checking",
        date(2026, 7, 10),
        money("-52.50"),
        splits=[
            SplitLine("groceries", money(-30)),
            SplitLine("household", money("-22.50")),
        ],
        payee="Target",
    )
    assert plan.account_balance("checking") == money("147.50")
    assert plan.activity("groceries", JUL) == money(-30)
    assert plan.activity("household", JUL) == money("-22.50")
    assert len(txn.lines()) == 2


def test_mismatched_split_is_rejected_at_write_time() -> None:
    plan = _plan()
    with pytest.raises(SplitMismatchError):
        plan.add_transaction(
            "checking",
            date(2026, 7, 10),
            money("-52.50"),
            splits=[
                SplitLine("groceries", money(-30)),
                SplitLine("household", money(-20)),
            ],
            payee="Target",
        )
    assert plan.account_balance("checking") == money(200)  # nothing was written
