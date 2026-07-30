"""DESIGN.md section 2: account taxonomy and creation rules."""

from datetime import date

import pytest

from ledger import AccountClass, AccountType, LedgerError, Plan, YMonth, money

JUL = YMonth(2026, 7)


def test_account_class_mapping_and_on_budget_flag() -> None:
    plan = Plan()
    checking = plan.add_account("checking", "Checking", AccountType.CHECKING)
    card = plan.add_account("card", "Card", AccountType.CREDIT_CARD)
    invest = plan.add_account("invest", "Investment", AccountType.INVESTMENT)
    assert checking.account_class is AccountClass.CASH and checking.on_budget
    assert card.account_class is AccountClass.CREDIT and card.on_budget
    assert invest.account_class is AccountClass.TRACKING and not invest.on_budget


def test_loan_accounts_require_pairing_and_a_rate() -> None:
    plan = Plan()
    plan.add_category_group("bills", "Bills")
    plan.add_category("mortgage_cat", "Rent/Mortgage", "bills")
    with pytest.raises(LedgerError):
        plan.add_account("mortgage", "Mortgage", AccountType.MORTGAGE)
    with pytest.raises(LedgerError):
        plan.add_account(
            "mortgage",
            "Mortgage",
            AccountType.MORTGAGE,
            paired_category_id="mortgage_cat",
        )
    loan = plan.add_account(
        "mortgage",
        "Mortgage",
        AccountType.MORTGAGE,
        paired_category_id="mortgage_cat",
        apr_percent="5",
    )
    assert loan.paired_category_id == "mortgage_cat"
    assert not loan.on_budget  # loans sit outside the budget partition


def test_cash_opening_balance_funds_rta() -> None:
    plan = Plan()
    plan.add_account(
        "checking",
        "Checking",
        AccountType.CHECKING,
        opening_balance=250,
        opening_date=date(2026, 7, 1),
    )
    assert plan.rta(JUL) == money(250)
    assert plan.account_balance("checking") == money(250)


def test_opening_balance_requires_a_date() -> None:
    plan = Plan()
    with pytest.raises(LedgerError):
        plan.add_account(
            "checking", "Checking", AccountType.CHECKING, opening_balance=250
        )
