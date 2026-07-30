"""DESIGN.md section 5.4: loan payment principal/interest decomposition."""

from datetime import date

import pytest

from ledger import RTA_INFLOW, ZERO, AccountType, LedgerError, Plan, YMonth, money

JUL = YMonth(2026, 7)
PAY_DAY = date(2026, 7, 15)


def _plan_with_auto_loan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("bills", "Bills")
    plan.add_category("car", "Car Payment", "bills")
    plan.add_account(
        "auto",
        "Test Auto Loan",
        AccountType.AUTO_LOAN,
        paired_category_id="car",
        apr_percent="6",
        opening_balance=-19700,
        opening_date=date(2026, 6, 1),
    )
    plan.add_transaction(
        "checking", date(2026, 7, 1), 2000, category_id=RTA_INFLOW, payee="Employer"
    )
    return plan


def test_payment_decomposes_into_principal_and_interest() -> None:
    plan = _plan_with_auto_loan()
    plan.assign("car", JUL, 400)
    breakdown = plan.record_loan_payment("checking", "auto", PAY_DAY, 400)
    assert breakdown.interest == money("98.50")  # 19,700 x 6% / 12
    assert breakdown.principal == money("301.50")
    # The balance improves by the principal only, never 1:1 with the payment.
    assert plan.account_balance("auto") == money("-19398.50")
    assert plan.account_balance("checking") == money(1600)


def test_payment_spends_the_paired_category_and_leaves_rta_alone() -> None:
    plan = _plan_with_auto_loan()
    plan.assign("car", JUL, 400)
    rta_before = plan.rta(JUL)
    plan.record_loan_payment("checking", "auto", PAY_DAY, 400)
    assert plan.activity("car", JUL) == money(-400)
    assert plan.available("car", JUL) == ZERO
    assert plan.rta(JUL) == rta_before


def test_interest_charge_is_a_structural_uncategorized_loan_transaction() -> None:
    plan = _plan_with_auto_loan()
    breakdown = plan.record_loan_payment("checking", "auto", PAY_DAY, 400)
    charge = breakdown.interest_charge
    assert charge is not None
    assert charge.account_id == "auto"
    assert charge.amount == money("-98.50")
    assert charge.category_id is None
    assert charge.payee_id is not None
    assert plan.payees[charge.payee_id].structural


def test_underpayment_below_interest_grows_the_balance() -> None:
    plan = _plan_with_auto_loan()
    breakdown = plan.record_loan_payment("checking", "auto", PAY_DAY, 50)
    assert breakdown.principal == money("-48.50")
    assert plan.account_balance("auto") == money("-19748.50")


def test_paid_off_loan_accrues_no_interest() -> None:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("bills", "Bills")
    plan.add_category("car", "Car Payment", "bills")
    plan.add_account(
        "auto",
        "Auto Loan",
        AccountType.AUTO_LOAN,
        paired_category_id="car",
        apr_percent="6",
    )
    plan.add_transaction(
        "checking", date(2026, 7, 1), 500, category_id=RTA_INFLOW, payee="Employer"
    )
    breakdown = plan.record_loan_payment("checking", "auto", PAY_DAY, 100)
    assert breakdown.interest == ZERO
    assert breakdown.interest_charge is None
    assert breakdown.principal == money(100)


def test_loan_transactions_cannot_be_categorized() -> None:
    plan = _plan_with_auto_loan()
    with pytest.raises(LedgerError):
        plan.add_transaction("auto", PAY_DAY, -25, category_id="car", payee="Lender")
    fee = plan.add_transaction("auto", PAY_DAY, -25, payee="Lender")  # fee is fine
    assert fee.category_id is None


def test_record_loan_payment_rejects_non_loan_accounts() -> None:
    plan = _plan_with_auto_loan()
    plan.add_account("savings", "Savings", AccountType.SAVINGS)
    with pytest.raises(LedgerError):
        plan.record_loan_payment("checking", "savings", PAY_DAY, 100)
