"""DESIGN.md sections 6 and 11 vectors 5-6: target funding formulas."""

from datetime import date

from ledger import (
    RTA_INFLOW,
    ZERO,
    AccountType,
    CustomSubMode,
    CustomTarget,
    MonthlyTarget,
    Plan,
    WeeklyTarget,
    YearlyTarget,
    YMonth,
    money,
)

JUL = YMonth(2026, 7)
TODAY = date(2026, 7, 29)
FRIDAY = 4
SATURDAY = 5


def _plan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("g", "Group")
    plan.add_category("util", "Utilities", "g")
    plan.add_transaction(
        "checking", date(2026, 7, 1), 1000, category_id=RTA_INFLOW, payee="Employer"
    )
    return plan


def test_have_balance_with_due_date_amortizes_inclusive_of_due_month() -> None:
    plan = _plan()
    target = CustomTarget(money(150), CustomSubMode.HAVE_BALANCE, YMonth(2026, 10))
    assert target.amount_needed(plan, "util", JUL, TODAY) == money("37.50")
    plan.assign("util", JUL, "37.50")
    assert target.amount_needed(plan, "util", JUL, TODAY) == ZERO


def test_have_balance_without_due_date_has_no_monthly_pressure() -> None:
    plan = _plan()
    target = CustomTarget(money(150), CustomSubMode.HAVE_BALANCE)
    assert target.amount_needed(plan, "util", JUL, TODAY) == ZERO


def test_yearly_uses_the_same_amortization_engine() -> None:
    plan = _plan()
    target = YearlyTarget(money(1200), YMonth(2027, 1))
    assert target.amount_needed(plan, "util", JUL, TODAY) == money("171.43")


def test_weekly_counts_remaining_weekday_occurrences_only() -> None:
    plan = _plan()
    saturdays = WeeklyTarget(money(20), SATURDAY)
    fridays = WeeklyTarget(money(20), FRIDAY)
    # Today is Wed Jul 29; the last Saturday of July was the 25th.
    assert saturdays.amount_needed(plan, "util", JUL, TODAY) == ZERO
    assert fridays.amount_needed(plan, "util", JUL, TODAY) == money(20)
    plan.assign("util", JUL, 20)
    assert fridays.amount_needed(plan, "util", JUL, TODAY) == ZERO


def test_monthly_and_custom_sub_modes_are_idempotent_after_funding() -> None:
    plan = _plan()
    monthly = MonthlyTarget(money(400))
    assert monthly.amount_needed(plan, "util", JUL, TODAY) == money(400)
    plan.assign("util", JUL, 400)
    assert monthly.amount_needed(plan, "util", JUL, TODAY) == ZERO

    set_aside = CustomTarget(money(400), CustomSubMode.SET_ASIDE)
    assert set_aside.amount_needed(plan, "util", JUL, TODAY) == ZERO

    fill_up = CustomTarget(money(500), CustomSubMode.FILL_UP_TO)
    assert fill_up.amount_needed(plan, "util", JUL, TODAY) == money(100)


def test_fill_up_to_tops_up_against_available_balance() -> None:
    plan = _plan()
    plan.assign("util", JUL, 100)
    plan.add_transaction(
        "checking", date(2026, 7, 10), -60, category_id="util", payee="Utility Co"
    )
    target = CustomTarget(money(150), CustomSubMode.FILL_UP_TO)
    assert target.amount_needed(plan, "util", JUL, TODAY) == money(110)
