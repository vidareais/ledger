"""ledger: a pure-Python envelope-budgeting engine. See DESIGN.md."""

from ledger.accounts import Account, AccountClass, AccountType
from ledger.categories import Category, CategoryGroup
from ledger.errors import LedgerError, SplitMismatchError, UnknownEntityError
from ledger.money import ZERO, Amount, money
from ledger.month import YMonth
from ledger.payees import Payee
from ledger.plan import PAYMENT_GROUP_ID, AutoAssignPreset, Plan
from ledger.schedules import Frequency, ScheduledTransaction
from ledger.targets import (
    BudgetView,
    CustomSubMode,
    CustomTarget,
    DebtPaymentTarget,
    MonthlyTarget,
    Target,
    WeeklyTarget,
    YearlyTarget,
    payoff_months,
    payoff_months_exact,
    required_payment,
    total_interest,
)
from ledger.transactions import RTA_INFLOW, ClearedStatus, SplitLine, Transaction

__all__ = [
    "PAYMENT_GROUP_ID",
    "RTA_INFLOW",
    "ZERO",
    "Account",
    "AccountClass",
    "AccountType",
    "Amount",
    "AutoAssignPreset",
    "BudgetView",
    "Category",
    "CategoryGroup",
    "ClearedStatus",
    "CustomSubMode",
    "CustomTarget",
    "DebtPaymentTarget",
    "Frequency",
    "LedgerError",
    "MonthlyTarget",
    "Payee",
    "Plan",
    "ScheduledTransaction",
    "SplitLine",
    "SplitMismatchError",
    "Target",
    "Transaction",
    "UnknownEntityError",
    "WeeklyTarget",
    "YMonth",
    "YearlyTarget",
    "money",
    "payoff_months",
    "payoff_months_exact",
    "required_payment",
    "total_interest",
]


def main() -> None:
    print("ledger is a library; run `uv run pytest` for the behavior suite")
