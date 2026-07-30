"""DESIGN.md section 10, vector 1: the tracking-account hard partition."""

from datetime import date

from ledger import RTA_INFLOW, AccountType, Plan, YMonth, money

JUL = YMonth(2026, 7)


def test_rta_inflow_on_budget_account_raises_rta() -> None:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_transaction(
        "checking", date(2026, 7, 2), 3000, category_id=RTA_INFLOW, payee="Employer"
    )
    assert plan.rta(JUL) == money(3000)
    assert plan.account_balance("checking") == money(3000)


def test_tracking_account_money_never_touches_rta() -> None:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_transaction(
        "checking", date(2026, 7, 2), 3000, category_id=RTA_INFLOW, payee="Employer"
    )
    plan.add_account(
        "invest",
        "Investment",
        AccountType.INVESTMENT,
        opening_balance=5000,
        opening_date=date(2026, 7, 1),
    )
    plan.add_transaction("invest", date(2026, 7, 3), 500)
    assert plan.rta(JUL) == money(3000)
    assert plan.account_balance("invest") == money(5500)


def test_net_worth_sums_every_account_class() -> None:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_transaction(
        "checking", date(2026, 7, 2), 3000, category_id=RTA_INFLOW, payee="Employer"
    )
    plan.add_account(
        "invest",
        "Investment",
        AccountType.INVESTMENT,
        opening_balance=5000,
        opening_date=date(2026, 7, 1),
    )
    plan.add_account(
        "card",
        "Card",
        AccountType.CREDIT_CARD,
        opening_balance=-250,
        opening_date=date(2026, 7, 1),
    )
    assert plan.net_worth() == money(3000 + 5000 - 250)
    # A credit account's negative opening balance is debt, not an RTA inflow.
    assert plan.rta(JUL) == money(3000)
