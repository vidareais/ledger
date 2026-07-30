"""DESIGN.md sections 4.2 and 10 vector 4: overspending rollover.

No explicit month-close call exists: August's numbers are pure derivations.
"""

from datetime import date

from ledger import RTA_INFLOW, ZERO, AccountType, Plan, YMonth, money

JUL = YMonth(2026, 7)
AUG = YMonth(2026, 8)


def _plan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("needs", "Needs")
    plan.add_category("transport", "Transportation", "needs")
    plan.add_category("home", "Home Renovation", "needs")
    plan.add_transaction(
        "checking", date(2026, 7, 1), 100, category_id=RTA_INFLOW, payee="Employer"
    )
    plan.assign("transport", JUL, 300)
    plan.add_transaction(
        "checking",
        date(2026, 7, 13),
        -350,
        category_id="transport",
        payee="Gas Station",
    )
    return plan


def test_positive_available_carries_forward() -> None:
    plan = _plan()
    plan.assign("home", JUL, 100)
    assert plan.carry_in("home", AUG) == money(100)
    assert plan.available("home", AUG) == money(100)


def test_uncovered_overspend_resets_category_and_docks_next_month_rta() -> None:
    plan = _plan()
    assert plan.available("transport", JUL) == money(-50)
    assert plan.carry_in("transport", AUG) == ZERO
    assert plan.available("transport", AUG) == ZERO
    assert plan.rta(AUG) == plan.rta(JUL) - money(50)


def test_covering_overspend_within_the_month_removes_the_penalty() -> None:
    plan = _plan()
    plan.assign("home", JUL, 100)
    rta_before_move = plan.rta(JUL)
    plan.move_money("home", "transport", JUL, 50)
    assert plan.rta(JUL) == rta_before_move
    assert plan.available("transport", JUL) == ZERO
    assert plan.available("home", JUL) == money(50)
    assert plan.rta(AUG) == plan.rta(JUL)
