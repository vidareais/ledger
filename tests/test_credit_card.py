"""DESIGN.md sections 5.3 and 10 vector 2: credit-card payment mechanics."""

from datetime import date

from ledger import RTA_INFLOW, ZERO, AccountType, Plan, YMonth, money

JUL = YMonth(2026, 7)


def _plan_with_card() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_account("card", "Card", AccountType.CREDIT_CARD)
    plan.add_category_group("wants", "Wants")
    plan.add_category("dining", "Dining Out", "wants")
    plan.add_transaction(
        "checking", date(2026, 7, 1), 100, category_id=RTA_INFLOW, payee="Employer"
    )
    return plan


def test_cc_spend_earmarks_budgeted_dollars_into_payment_category() -> None:
    plan = _plan_with_card()
    plan.assign("dining", JUL, 50)
    plan.add_transaction(
        "card", date(2026, 7, 10), -30, category_id="dining", payee="Cafe"
    )
    assert plan.available("dining", JUL) == money(20)
    assert plan.available("payment:card", JUL) == money(30)
    assert plan.rta(JUL) == money(50)
    assert plan.account_balance("card") == money(-30)


def test_paying_the_card_drains_the_payment_category() -> None:
    plan = _plan_with_card()
    plan.assign("dining", JUL, 50)
    plan.add_transaction(
        "card", date(2026, 7, 10), -30, category_id="dining", payee="Cafe"
    )
    out_leg, in_leg = plan.add_transfer("checking", "card", date(2026, 7, 20), 30)
    assert plan.available("payment:card", JUL) == ZERO
    assert plan.account_balance("card") == ZERO
    assert plan.account_balance("checking") == money(70)
    assert plan.rta(JUL) == money(50)
    assert out_leg.transfer_id == in_leg.transfer_id
    assert out_leg.category_id is None and in_leg.category_id is None


def test_cc_overspend_earmarks_only_the_funded_portion() -> None:
    plan = _plan_with_card()
    plan.assign("dining", JUL, 10)
    plan.add_transaction(
        "card", date(2026, 7, 10), -30, category_id="dining", payee="Cafe"
    )
    assert plan.available("payment:card", JUL) == money(10)
    assert plan.available("dining", JUL) == money(-20)


def test_line_of_credit_gets_a_payment_category_too() -> None:
    plan = Plan()
    plan.add_account("loc", "Test LOC", AccountType.LINE_OF_CREDIT)
    assert "payment:loc" in plan.categories
    assert plan.categories["payment:loc"].is_payment_category
