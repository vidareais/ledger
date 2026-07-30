"""DESIGN.md sections 6.4 and 10 vector 7: debt-payment amortization math.

Expected figures come from the reference app's display and are rounded
there, hence the small tolerances.
"""

from decimal import Decimal

from ledger import money, payoff_months, required_payment, total_interest

AUTO_LOAN = money(19700)
AUTO_APR = Decimal(6)
MORTGAGE = money("199633.33")
MORTGAGE_APR = Decimal(5)


def _assert_close(actual: Decimal | None, expected: str, tolerance: str) -> None:
    assert actual is not None
    assert abs(actual - Decimal(expected)) <= Decimal(tolerance)


def test_payoff_months_matches_observed_auto_loan_projections() -> None:
    assert payoff_months(AUTO_LOAN, AUTO_APR, money(400)) == 57
    assert payoff_months(AUTO_LOAN, AUTO_APR, money(600)) == 36
    assert payoff_months(AUTO_LOAN, AUTO_APR, money(300)) == 80


def test_total_interest_matches_observed_figures() -> None:
    _assert_close(total_interest(AUTO_LOAN, AUTO_APR, money(400)), "2972.05", "1")
    _assert_close(total_interest(AUTO_LOAN, AUTO_APR, money(600)), "1872.88", "1")
    _assert_close(total_interest(AUTO_LOAN, AUTO_APR, money(300)), "4239.24", "1")
    _assert_close(total_interest(MORTGAGE, MORTGAGE_APR, money(1200)), "141337.16", "5")
    _assert_close(total_interest(MORTGAGE, MORTGAGE_APR, money(2000)), "58994.14", "5")


def test_payoff_date_back_solves_the_payment() -> None:
    # Jul 2026 -> Jan 2030 is 42 months; the app displayed $521.19/mo.
    assert required_payment(AUTO_LOAN, AUTO_APR, 42) == Decimal("521.19")


def test_payment_at_or_below_monthly_interest_never_amortizes() -> None:
    monthly_interest = AUTO_LOAN * AUTO_APR / 100 / 12  # 98.50
    assert payoff_months(AUTO_LOAN, AUTO_APR, money("98.50")) is None
    assert payoff_months(AUTO_LOAN, AUTO_APR, money(50)) is None
    assert total_interest(AUTO_LOAN, AUTO_APR, money(0)) is None
    assert monthly_interest == Decimal("98.50")


def test_zero_rate_degenerates_to_simple_division() -> None:
    assert payoff_months(money(1200), Decimal(0), money(100)) == 12
    assert required_payment(money(1200), Decimal(0), 12) == money(100)
