"""Target types and their funding formulas (DESIGN.md section 6).

Every formula subtracts the amount already assigned this month, so the
"needed" figure reaches zero once the ask is funded and Auto-Assign is
idempotent.
"""

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum, auto
from typing import Protocol

from ledger.money import ZERO, money
from ledger.month import YMonth


class BudgetView(Protocol):
    """The slice of plan state a target needs to compute its ask."""

    def assigned_for(self, category_id: str, month: YMonth) -> Decimal: ...

    def carry_in(self, category_id: str, month: YMonth) -> Decimal: ...

    def activity(self, category_id: str, month: YMonth) -> Decimal: ...

    def available(self, category_id: str, month: YMonth) -> Decimal: ...


class CustomSubMode(Enum):
    SET_ASIDE = auto()
    FILL_UP_TO = auto()
    HAVE_BALANCE = auto()


def _amortized_ask(
    view: BudgetView,
    category_id: str,
    month: YMonth,
    amount: Decimal,
    due_month: YMonth,
) -> Decimal:
    """Section 6.2: spread the remaining goal over the months through the
    due month, inclusive of both endpoints."""
    balance = view.carry_in(category_id, month) + view.activity(category_id, month)
    to_go = money(amount) - balance
    if to_go <= ZERO:
        return ZERO
    months = max(1, month.months_until_inclusive(due_month))
    ask = money(to_go / months)
    return max(ZERO, ask - view.assigned_for(category_id, month))


@dataclass(frozen=True)
class MonthlyTarget:
    amount: Decimal

    def amount_needed(
        self, view: BudgetView, category_id: str, month: YMonth, today: date
    ) -> Decimal:
        return max(ZERO, money(self.amount) - view.assigned_for(category_id, month))


@dataclass(frozen=True)
class CustomTarget:
    amount: Decimal
    sub_mode: CustomSubMode
    due_month: YMonth | None = None

    def amount_needed(
        self, view: BudgetView, category_id: str, month: YMonth, today: date
    ) -> Decimal:
        if self.sub_mode is CustomSubMode.SET_ASIDE:
            assigned = view.assigned_for(category_id, month)
            return max(ZERO, money(self.amount) - assigned)
        if self.sub_mode is CustomSubMode.FILL_UP_TO:
            return max(ZERO, money(self.amount) - view.available(category_id, month))
        if self.due_month is None:
            return ZERO
        return _amortized_ask(view, category_id, month, self.amount, self.due_month)


@dataclass(frozen=True)
class YearlyTarget:
    amount: Decimal
    due_month: YMonth

    def amount_needed(
        self, view: BudgetView, category_id: str, month: YMonth, today: date
    ) -> Decimal:
        return _amortized_ask(view, category_id, month, self.amount, self.due_month)


@dataclass(frozen=True)
class WeeklyTarget:
    amount: Decimal
    weekday: int

    def amount_needed(
        self, view: BudgetView, category_id: str, month: YMonth, today: date
    ) -> Decimal:
        occurrences = self._remaining_occurrences(month, today)
        assigned = view.assigned_for(category_id, month)
        return max(ZERO, money(self.amount * occurrences) - assigned)

    def _remaining_occurrences(self, month: YMonth, today: date) -> int:
        start = today.day if YMonth.of(today) == month else 1
        return sum(
            1
            for day in range(start, month.last_day() + 1)
            if date(month.year, month.month, day).weekday() == self.weekday
        )


@dataclass(frozen=True)
class DebtPaymentTarget:
    account_id: str
    apr_percent: Decimal
    monthly_payment: Decimal

    def amount_needed(
        self, view: BudgetView, category_id: str, month: YMonth, today: date
    ) -> Decimal:
        assigned = view.assigned_for(category_id, month)
        return max(ZERO, money(self.monthly_payment) - assigned)


type Target = (
    MonthlyTarget | CustomTarget | YearlyTarget | WeeklyTarget | DebtPaymentTarget
)


def _monthly_rate(apr_percent: Decimal) -> Decimal:
    return apr_percent / Decimal(100) / Decimal(12)


def payoff_months_exact(
    balance: Decimal, apr_percent: Decimal, payment: Decimal
) -> Decimal | None:
    """Fractional months to pay off `balance` at a fixed monthly `payment`.

    Section 6.4: n = -ln(1 - r*P/M) / ln(1 + r). Returns None when the
    payment never amortizes the balance (payment <= monthly interest).
    """
    principal = abs(balance)
    if payment <= ZERO:
        return None
    if principal == ZERO:
        return Decimal(0)
    rate = _monthly_rate(apr_percent)
    if rate == 0:
        return principal / payment
    if payment <= principal * rate:
        return None
    return -(1 - rate * principal / payment).ln() / (1 + rate).ln()


def payoff_months(
    balance: Decimal, apr_percent: Decimal, payment: Decimal
) -> int | None:
    """Whole months (rounded up) to pay off `balance`; None if it never pays off."""
    exact = payoff_months_exact(balance, apr_percent, payment)
    return None if exact is None else math.ceil(exact)


def total_interest(
    balance: Decimal, apr_percent: Decimal, payment: Decimal
) -> Decimal | None:
    """Interest over the full payoff: exact months x payment - principal."""
    exact = payoff_months_exact(balance, apr_percent, payment)
    return None if exact is None else money(exact * payment - abs(balance))


def required_payment(balance: Decimal, apr_percent: Decimal, months: int) -> Decimal:
    """Fixed payment that amortizes `balance` in exactly `months` months."""
    if months <= 0:
        raise ValueError("months must be positive")
    principal = abs(balance)
    rate = _monthly_rate(apr_percent)
    if rate == 0:
        return money(principal / months)
    return money(principal * rate / (1 - (1 + rate) ** -months))
