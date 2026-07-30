"""Persistence: to_dict/from_dict round-trips and the JSON plan store."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ledger import (
    RTA_INFLOW,
    AccountType,
    CustomSubMode,
    CustomTarget,
    DebtPaymentTarget,
    FlagColor,
    Frequency,
    JsonPlanStore,
    MonthlyTarget,
    PersistenceError,
    Plan,
    PlanStore,
    SplitLine,
    WeeklyTarget,
    YMonth,
    money,
)

JUL = YMonth(2026, 7)
AUG = YMonth(2026, 8)


def _rich_plan() -> Plan:
    """One plan exercising every serialized feature: all account classes,
    every target family, splits, transfers, credit and loan mechanics,
    schedules, snoozes, future-month assignment, and reconciliation."""
    plan = Plan()
    plan.add_category_group("bills", "Bills")
    plan.add_category_group("wants", "Wants")
    plan.add_category("phone", "Phone", "bills")
    plan.add_category("car", "Car Payment", "bills")
    plan.add_category("dining", "Dining Out", "wants")
    plan.add_category("fun", "Fun Money", "wants")
    plan.set_target("phone", MonthlyTarget(money(45)))
    plan.set_target(
        "fun", CustomTarget(money(150), CustomSubMode.HAVE_BALANCE, YMonth(2026, 10))
    )
    plan.set_target("dining", WeeklyTarget(money(20), 4))
    plan.set_target("car", DebtPaymentTarget("auto", Decimal(6), money(400)))
    plan.add_account(
        "checking",
        "Checking",
        AccountType.CHECKING,
        note="Main account",
        opening_balance=250,
        opening_date=date(2026, 6, 28),
    )
    plan.add_account("savings", "Savings", AccountType.SAVINGS)
    plan.add_account("card", "Card", AccountType.CREDIT_CARD)
    plan.add_account(
        "auto",
        "Auto Loan",
        AccountType.AUTO_LOAN,
        paired_category_id="car",
        apr_percent="6",
        opening_balance=-19700,
        opening_date=date(2026, 6, 28),
    )
    plan.add_account(
        "invest",
        "Investment",
        AccountType.INVESTMENT,
        opening_balance=5000,
        opening_date=date(2026, 6, 28),
    )
    plan.add_transaction(
        "checking",
        date(2026, 7, 1),
        3000,
        category_id=RTA_INFLOW,
        payee="Employer",
        cleared=True,
    )
    plan.assign("phone", JUL, 45)
    plan.assign("dining", JUL, 100)
    plan.assign("car", JUL, 400)
    plan.assign("dining", AUG, 50)  # future-month assignment
    plan.add_transaction(
        "checking",
        date(2026, 7, 3),
        money("-52.50"),
        splits=[SplitLine("dining", money(-30)), SplitLine("fun", money("-22.50"))],
        payee="Target",
    )
    plan.add_transaction(
        "card",
        date(2026, 7, 5),
        -25,
        category_id="dining",
        payee="Cafe",
        flag_color=FlagColor.GREEN,
    )
    plan.add_transfer("checking", "savings", date(2026, 7, 6), 150)
    plan.add_transfer("checking", "card", date(2026, 7, 8), 25)
    plan.record_loan_payment("checking", "auto", date(2026, 7, 15), 400)
    plan.add_schedule(
        "checking",
        date(2026, 7, 20),
        "-15.99",
        Frequency.MONTHLY,
        category_id="phone",
        payee="Netflix",
        today=date(2026, 7, 20),
    )
    plan.snooze("phone", AUG)
    plan.reconcile(
        "checking", date(2026, 7, 21), plan.cleared_balance("checking") - money(10)
    )
    plan.set_category_hidden("fun", True)
    plan.close_account("savings")
    return plan


def test_round_trip_reproduces_the_document_exactly() -> None:
    plan = _rich_plan()
    document = plan.to_dict()
    assert Plan.from_dict(document).to_dict() == document


def test_round_trip_preserves_derived_figures() -> None:
    plan = _rich_plan()
    reloaded = Plan.from_dict(plan.to_dict())
    for month in (JUL, AUG):
        assert reloaded.rta(month) == plan.rta(month)
        for category_id in plan.categories:
            assert reloaded.available(category_id, month) == plan.available(
                category_id, month
            )
    assert reloaded.net_worth() == plan.net_worth()
    assert reloaded.age_of_money() == plan.age_of_money()
    assert reloaded.display_order() == plan.display_order()
    assert reloaded.cleared_balance("checking") == plan.cleared_balance("checking")
    assert reloaded.pending_approval() == plan.pending_approval()


def test_reloaded_plan_keeps_operating_without_id_collisions() -> None:
    plan = Plan.from_dict(_rich_plan().to_dict())
    existing_ids = {t.id for t in plan.transactions()}
    txn = plan.add_transaction(
        "checking", date(2026, 7, 22), -5, category_id="dining", payee="Cafe"
    )
    assert txn.id not in existing_ids
    materialized = plan.materialize_due(date(2026, 8, 20))
    assert [t.date for t in materialized] == [date(2026, 8, 20)]


def test_json_store_round_trips_via_disk(tmp_path: Path) -> None:
    store: PlanStore = JsonPlanStore(tmp_path / "plan.json")
    assert not store.exists()
    plan = _rich_plan()
    store.save(plan)
    assert store.exists()
    assert store.load().to_dict() == plan.to_dict()
    store.save(plan)  # overwriting an existing document is fine
    assert store.load().to_dict() == plan.to_dict()


def test_loading_a_missing_document_raises(tmp_path: Path) -> None:
    with pytest.raises(PersistenceError):
        JsonPlanStore(tmp_path / "absent.json").load()


def test_corrupt_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PersistenceError):
        JsonPlanStore(path).load()


def test_wrong_format_or_version_raises() -> None:
    with pytest.raises(PersistenceError):
        Plan.from_dict({"format": "something-else", "version": 1})
    document = _rich_plan().to_dict()
    document["version"] = 99
    with pytest.raises(PersistenceError):
        Plan.from_dict(document)


def test_malformed_document_raises() -> None:
    document = _rich_plan().to_dict()
    document["transactions"][0]["status"] = "NOT_A_STATUS"
    with pytest.raises(PersistenceError):
        Plan.from_dict(document)
