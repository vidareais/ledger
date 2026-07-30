"""DESIGN.md section 8.2: recurring transactions and the scheduling boundary."""

from datetime import date

import pytest

from ledger import (
    AccountType,
    Frequency,
    LedgerError,
    Plan,
    YMonth,
    money,
)

JUL = YMonth(2026, 7)


def _plan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("bills", "Bills")
    plan.add_category("subs", "Subscriptions", "bills")
    return plan


def test_creating_a_schedule_materializes_the_current_occurrence() -> None:
    plan = _plan()
    schedule, created = plan.add_schedule(
        "checking",
        date(2026, 7, 15),
        "-15.99",
        Frequency.MONTHLY,
        category_id="subs",
        payee="Netflix",
        today=date(2026, 7, 15),
    )
    assert len(created) == 1
    txn = created[0]
    assert txn.date == date(2026, 7, 15)
    assert txn.amount == money("-15.99")
    assert not txn.approved  # real but pending review
    assert plan.pending_approval() == (txn,)
    assert schedule.next_date == date(2026, 8, 15)  # a full period ahead
    assert plan.activity("subs", JUL) == money("-15.99")  # counts immediately


def test_future_schedule_waits_for_its_date() -> None:
    plan = _plan()
    schedule, created = plan.add_schedule(
        "checking",
        date(2026, 8, 1),
        -10,
        Frequency.MONTHLY,
        category_id="subs",
        today=date(2026, 7, 15),
    )
    assert created == []
    assert plan.materialize_due(date(2026, 7, 31)) == []
    materialized = plan.materialize_due(date(2026, 8, 1))
    assert [t.date for t in materialized] == [date(2026, 8, 1)]
    assert schedule.next_date == date(2026, 9, 1)


def test_catch_up_materializes_every_missed_occurrence() -> None:
    plan = _plan()
    _, created = plan.add_schedule(
        "checking",
        date(2026, 7, 3),
        -20,
        Frequency.WEEKLY,
        category_id="subs",
        today=date(2026, 7, 17),
    )
    assert [t.date for t in created] == [
        date(2026, 7, 3),
        date(2026, 7, 10),
        date(2026, 7, 17),
    ]
    assert plan.account_balance("checking") == money(-60)


def test_monthly_anchor_day_clamps_without_drifting() -> None:
    plan = _plan()
    schedule, _ = plan.add_schedule(
        "checking", date(2027, 1, 31), -50, Frequency.MONTHLY, category_id="subs"
    )
    created = plan.materialize_due(date(2027, 4, 30))
    assert [t.date for t in created] == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
    ]
    assert schedule.next_date == date(2027, 5, 31)  # springs back to the anchor


def test_approve_flips_the_review_flag() -> None:
    plan = _plan()
    _, created = plan.add_schedule(
        "checking",
        date(2026, 7, 15),
        -10,
        Frequency.MONTHLY,
        category_id="subs",
        today=date(2026, 7, 15),
    )
    approved = plan.approve(created[0].id)
    assert approved.approved
    assert plan.pending_approval() == ()


def test_deleted_schedule_stops_materializing() -> None:
    plan = _plan()
    schedule, _ = plan.add_schedule(
        "checking", date(2026, 8, 1), -10, Frequency.MONTHLY, category_id="subs"
    )
    plan.delete_schedule(schedule.id)
    assert plan.materialize_due(date(2026, 12, 1)) == []


def test_budget_account_schedule_requires_a_category() -> None:
    plan = _plan()
    with pytest.raises(LedgerError):
        plan.add_schedule("checking", date(2026, 8, 1), -10, Frequency.MONTHLY)
