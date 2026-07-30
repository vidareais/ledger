"""DESIGN.md sections 7 and 10 vectors 3 and 8: the two Auto-Assign families."""

from datetime import date
from decimal import Decimal

from ledger import (
    RTA_INFLOW,
    ZERO,
    AccountType,
    AutoAssignPreset,
    MonthlyTarget,
    Plan,
    YMonth,
    money,
)

JUN = YMonth(2026, 6)
JUL = YMonth(2026, 7)
TODAY = date(2026, 7, 15)


def _plan_with_five_targets() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("bills", "Bills")
    plan.add_category_group("needs", "Needs")
    plan.add_category_group("wants", "Wants")
    plan.add_category("phone", "Phone & Internet", "bills")
    plan.add_category("medical", "Medical expenses", "needs")
    plan.add_category("emergency", "Emergency fund", "needs")
    plan.add_category("vacation", "Vacation", "wants")
    plan.add_category("club", "Club subscription", "wants")
    for category_id, amount in [
        ("phone", 400),
        ("medical", 150),
        ("emergency", 200),
        ("vacation", 250),
        ("club", 50),
    ]:
        plan.set_target(category_id, MonthlyTarget(amount=money(amount)))
    plan.add_transaction(
        "checking", date(2026, 7, 1), "964.01", category_id=RTA_INFLOW, payee="Employer"
    )
    return plan


def test_underfunded_walk_is_positional_greedy_with_one_partial_boundary() -> None:
    plan = _plan_with_five_targets()
    proposal = plan.preview_underfunded(JUL, TODAY)
    assert proposal == {
        "phone": money(400),
        "medical": money(150),
        "emergency": money(200),
        "vacation": money("214.01"),
    }
    assert (
        "club" not in proposal
    )  # strictly zero past the boundary, despite needing $50


def test_applying_the_underfunded_plan_is_atomic_and_idempotent() -> None:
    plan = _plan_with_five_targets()
    plan.apply_assignments(JUL, plan.preview_underfunded(JUL, TODAY))
    assert plan.rta(JUL) == ZERO
    assert plan.preview_underfunded(JUL, TODAY) == {}


def test_underfunded_skips_snoozed_categories() -> None:
    plan = _plan_with_five_targets()
    plan.snooze("phone", JUL)
    proposal = plan.preview_underfunded(JUL, TODAY)
    assert "phone" not in proposal
    assert proposal["club"] == money(50)  # pool now reaches the bottom


def _plan_with_history() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("g", "Group")
    for category_id, name in [
        ("reno", "Home Renovation"),
        ("groceries", "Groceries"),
        ("transport", "Transportation"),
        ("dining", "Dining out"),
    ]:
        plan.add_category(category_id, name, "g")
    plan.add_transaction(
        "checking", date(2026, 6, 1), 100, category_id=RTA_INFLOW, payee="Employer"
    )
    plan.assign("reno", JUN, 500)
    plan.assign("groceries", JUN, 400)
    plan.assign("transport", JUN, 300)
    plan.add_transaction(
        "checking",
        date(2026, 6, 20),
        -150,
        category_id="groceries",
        payee="Whole Foods",
    )
    plan.assign("dining", JUL, 30)
    return plan


def test_presets_are_direct_sets_that_can_pull_categories_down() -> None:
    plan = _plan_with_history()
    proposal = plan.preview_preset(AutoAssignPreset.ASSIGNED_LAST_MONTH, JUL)
    assert proposal["reno"] == money(500)
    assert proposal["groceries"] == money(400)
    assert proposal["transport"] == money(300)
    assert proposal["dining"] == ZERO  # pulled down: June had nothing assigned


def test_presets_apply_uncapped_and_can_push_rta_negative() -> None:
    plan = _plan_with_history()
    proposal = plan.preview_preset(AutoAssignPreset.ASSIGNED_LAST_MONTH, JUL)
    plan.apply_assignments(JUL, proposal)
    assert plan.rta(JUL) < ZERO
    assert plan.assigned_for("dining", JUL) == ZERO


def test_average_with_one_history_month_collapses_to_that_month() -> None:
    plan = _plan_with_history()
    last = plan.preview_preset(AutoAssignPreset.ASSIGNED_LAST_MONTH, JUL)
    average = plan.preview_preset(AutoAssignPreset.AVERAGE_ASSIGNED, JUL)
    assert average == last


def test_spent_last_month_sources_outflows() -> None:
    plan = _plan_with_history()
    proposal = plan.preview_preset(AutoAssignPreset.SPENT_LAST_MONTH, JUL)
    assert proposal["groceries"] == money(150)
    assert proposal["reno"] == ZERO


def test_presets_with_no_history_propose_zero() -> None:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("g", "Group")
    plan.add_category("a", "A", "g")
    proposal = plan.preview_preset(AutoAssignPreset.ASSIGNED_LAST_MONTH, JUL)
    assert proposal == {"a": Decimal("0.00")}
