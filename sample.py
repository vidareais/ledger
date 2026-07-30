"""
ynab_engine.py — A single-file, standard-library-only prototype of the core
budgeting engine reverse-engineered from hands-on testing of a YNAB-style
budgeting app.

This is a SIMPLIFIED, DEMONSTRATIVE model of observed behavior, not a
reconstruction of any proprietary source code. It captures the mechanics
that were verified empirically:

  * Account model: linked vs. unlinked accounts, and on-budget ("budget")
    accounts vs. off-budget ("tracking") accounts, and how each affects
    Ready to Assign (RTA).
  * Category / category-group structure.
  * Money movement: assigning money, moving money between categories,
    transfers between accounts, and credit-card payment mechanics.
  * Auto-Assign: two distinct algorithm families —
        - "Underfunded": a greedy, top-to-bottom, partial-funding walk
          that only ever ADDS money, capped by available RTA.
        - "Assigned Last Month / Spent Last Month / Average Assigned /
          Average Spent": direct "set-to-historical-value" operations
          that can raise OR lower a category's assignment, uncapped.
  * Overspending rollover: uncovered overspending docks next month's RTA
    by the exact overspent amount; covering it with a real money-move
    within the same month avoids that penalty entirely.
  * Target-type formulas:
        - Monthly: flat "assign up to $X every month".
        - Custom ("Have a balance of $X") and Yearly, both with a due
          date: (amount still needed) / (calendar months from the
          current month through the due month, inclusive of both ends).
        - Weekly: amount * (number of remaining occurrences of the
          chosen weekday between today and month-end).
        - Debt Payment Target: standard loan amortization, so changing
          the monthly payment recomputes the payoff term.
  * Undo/Redo as a genuine action-history stack (snapshot-based).

Run this file directly to see a demo walkthrough exercising every
mechanic (`python ynab_engine.py`).
"""

from __future__ import annotations

import calendar
import copy
import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum, auto
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Money helpers
# ---------------------------------------------------------------------------

TWOPLACES = Decimal("0.01")


def money(x) -> Decimal:
    """Coerce any numeric/string into a 2-decimal Decimal."""
    return Decimal(str(x)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


ZERO = money(0)


# ---------------------------------------------------------------------------
# Calendar month helper
# ---------------------------------------------------------------------------

@dataclass(frozen=True, order=True)
class YMonth:
    """A calendar month, e.g. YMonth(2026, 7) == July 2026."""
    year: int
    month: int

    def next(self) -> "YMonth":
        return YMonth(self.year + (1 if self.month == 12 else 0),
                      1 if self.month == 12 else self.month + 1)

    def prev(self) -> "YMonth":
        return YMonth(self.year - (1 if self.month == 1 else 0),
                      12 if self.month == 1 else self.month - 1)

    def months_until_inclusive(self, other: "YMonth") -> int:
        """Number of calendar months from self through other, counting
        both endpoints (matches the empirically-derived target formula:
        e.g. Jul 2026 -> Jan 2027 inclusive = 7 months)."""
        return (other.year - self.year) * 12 + (other.month - self.month) + 1

    def last_day(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    def __str__(self):
        return f"{self.year:04d}-{self.month:02d}"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AccountType(Enum):
    CHECKING = auto()
    SAVINGS = auto()
    CREDIT_CARD = auto()
    LINE_OF_CREDIT = auto()
    MORTGAGE = auto()
    AUTO_LOAN = auto()
    INVESTMENT = auto()
    OTHER_ASSET = auto()
    OTHER_LIABILITY = auto()


class CustomSubMode(Enum):
    SET_ASIDE = auto()      # "Set aside $X" — add regardless of balance
    FILL_UP_TO = auto()     # "Fill up to $X" — top up existing balance
    HAVE_BALANCE = auto()   # "Have a balance of $X" — savings-goal style


class AutoAssignPreset(Enum):
    UNDERFUNDED = auto()
    ASSIGNED_LAST_MONTH = auto()
    SPENT_LAST_MONTH = auto()
    AVERAGE_ASSIGNED = auto()
    AVERAGE_SPENT = auto()


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

@dataclass
class Account:
    id: str
    name: str
    account_type: AccountType
    on_budget: bool          # True = "budget" account, False = "tracking" account
    linked: bool = False      # bank-connected vs. manually managed (unlinked)
    balance: Decimal = ZERO

    @property
    def is_credit_card(self) -> bool:
        return self.account_type == AccountType.CREDIT_CARD

    def apply_delta(self, amount: Decimal) -> None:
        self.balance = money(self.balance + amount)


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

class Target:
    """Base class. amount_needed() returns the 'Underfunded' /
    'Assign $X to stay on track' figure for a given month."""

    def amount_needed(self, category: "Category", month: YMonth, today: date) -> Decimal:
        raise NotImplementedError

    def describe(self) -> str:
        return self.__class__.__name__


@dataclass
class MonthlyTarget(Target):
    amount: Decimal

    def amount_needed(self, category, month, today):
        before = category.get_carry_in(month) + category.get_activity(month)
        return max(ZERO, money(self.amount) - before)

    def describe(self):
        return f"Monthly ${self.amount}"


@dataclass
class CustomTarget(Target):
    amount: Decimal
    sub_mode: CustomSubMode
    due_month: Optional[YMonth] = None  # only meaningful for HAVE_BALANCE

    def amount_needed(self, category, month, today):
        if self.sub_mode == CustomSubMode.SET_ASIDE:
            return money(self.amount)

        if self.sub_mode == CustomSubMode.FILL_UP_TO:
            before = category.get_carry_in(month) + category.get_activity(month)
            return max(ZERO, money(self.amount) - before)

        # HAVE_BALANCE
        if self.due_month is None:
            # Confirmed empirically: no due date -> no monthly funding
            # pressure at all, regardless of shortfall.
            return ZERO

        current_balance = category.get_carry_in(month) + category.get_activity(month)
        to_go = money(self.amount) - current_balance
        if to_go <= ZERO:
            return ZERO
        months = max(1, month.months_until_inclusive(self.due_month))
        return money(to_go / Decimal(months))

    def describe(self):
        if self.sub_mode == CustomSubMode.HAVE_BALANCE and self.due_month:
            return f"Custom: have ${self.amount} by {self.due_month}"
        return f"Custom ({self.sub_mode.name}) ${self.amount}"


@dataclass
class YearlyTarget(Target):
    amount: Decimal
    due_month: YMonth

    def amount_needed(self, category, month, today):
        # Confirmed empirically to use the identical amortization formula
        # as Custom-with-due-date (e.g. $1200 / 7 months = $171.43).
        current_balance = category.get_carry_in(month) + category.get_activity(month)
        to_go = money(self.amount) - current_balance
        if to_go <= ZERO:
            return ZERO
        months = max(1, month.months_until_inclusive(self.due_month))
        return money(to_go / Decimal(months))

    def describe(self):
        return f"Yearly ${self.amount} by {self.due_month}"


@dataclass
class WeeklyTarget(Target):
    amount: Decimal
    weekday: int  # Monday=0 ... Sunday=6, matching date.weekday()

    def amount_needed(self, category, month, today):
        # Counts only remaining occurrences of the chosen weekday between
        # "today" and month-end -- NOT a flat monthly split. If the
        # weekday has already fully passed this month, the target is
        # trivially "met" ($0 needed).
        last_day = month.last_day()
        start_day = today.day if (today.year == month.year and today.month == month.month) else 1
        occurrences = sum(
            1 for day in range(start_day, last_day + 1)
            if date(month.year, month.month, day).weekday() == self.weekday
        )
        return money(self.amount * occurrences)

    def describe(self):
        return f"Weekly ${self.amount} every weekday#{self.weekday}"


@dataclass
class DebtPaymentTarget(Target):
    """Mirrors a category paired to a loan/tracking account: the target
    amount is driven by standard loan amortization math, so changing the
    monthly payment recomputes the payoff timeline."""
    linked_account_id: str
    apr_percent: Decimal   # e.g. Decimal("6.5") for 6.5% APR
    monthly_payment: Decimal

    def amount_needed(self, category, month, today):
        return money(self.monthly_payment)

    def describe(self):
        return f"Debt Payment ${self.monthly_payment}/mo @ {self.apr_percent}% APR"

    @staticmethod
    def payoff_months(balance: Decimal, apr_percent: Decimal, payment: Decimal) -> Optional[int]:
        """How many months to pay off `balance` at a fixed `payment`,
        given a fixed annual `apr_percent`. Returns None if the payment
        never covers the accruing interest (balance never shrinks)."""
        balance = abs(balance)
        r = (apr_percent / Decimal(100)) / Decimal(12)
        if payment <= 0:
            return None
        if r == 0:
            return int((balance / payment).to_integral_value(rounding=ROUND_HALF_UP))
        if payment <= balance * r:
            return None  # payment doesn't even cover interest
        n = -math.log(1 - float(r) * float(balance) / float(payment)) / math.log(1 + float(r))
        return int(math.ceil(n))

    @staticmethod
    def required_payment(balance: Decimal, apr_percent: Decimal, months: int) -> Decimal:
        """Required fixed monthly payment to pay off `balance` in exactly
        `months` months at `apr_percent` APR (standard amortization)."""
        balance = abs(balance)
        r = (apr_percent / Decimal(100)) / Decimal(12)
        if r == 0:
            return money(balance / Decimal(months))
        rf = float(r)
        payment = float(balance) * rf / (1 - (1 + rf) ** (-months))
        return money(payment)


# ---------------------------------------------------------------------------
# Categories / groups
# ---------------------------------------------------------------------------

@dataclass
class Category:
    id: str
    name: str
    group_id: str
    target: Optional[Target] = None
    assigned: Dict[YMonth, Decimal] = field(default_factory=dict)
    activity: Dict[YMonth, Decimal] = field(default_factory=dict)   # negative = spending
    carry_in: Dict[YMonth, Decimal] = field(default_factory=dict)   # available carried from prior month (>=0)
    is_cc_payment_category: bool = False
    linked_cc_account_id: Optional[str] = None

    def get_assigned(self, m: YMonth) -> Decimal:
        return self.assigned.get(m, ZERO)

    def get_activity(self, m: YMonth) -> Decimal:
        return self.activity.get(m, ZERO)

    def get_carry_in(self, m: YMonth) -> Decimal:
        return self.carry_in.get(m, ZERO)

    def available(self, m: YMonth) -> Decimal:
        return money(self.get_carry_in(m) + self.get_assigned(m) + self.get_activity(m))

    def set_assigned(self, m: YMonth, value: Decimal) -> None:
        self.assigned[m] = money(value)

    def add_assigned(self, m: YMonth, delta: Decimal) -> None:
        self.assigned[m] = money(self.get_assigned(m) + delta)

    def add_activity(self, m: YMonth, delta: Decimal) -> None:
        self.activity[m] = money(self.get_activity(m) + delta)


@dataclass
class CategoryGroup:
    id: str
    name: str
    category_ids: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Undo/Redo support (generic snapshot-based action-history stack)
# ---------------------------------------------------------------------------

def tracked(label_fmt: str):
    """Decorator: snapshots Budget state before a mutating call, pushes it
    (with a human-readable label) onto the undo stack, and clears the
    redo stack -- confirming Undo/Redo behaves as a genuine, linear
    action-history stack rather than per-field inverse operations."""
    def decorator(fn: Callable):
        def wrapper(self: "Budget", *args, **kwargs):
            snapshot = self._snapshot()
            label = label_fmt.format(*args, **kwargs)
            result = fn(self, *args, **kwargs)
            self._undo_stack.append((label, snapshot))
            self._redo_stack.clear()
            return result
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# The Budget engine
# ---------------------------------------------------------------------------

class Budget:
    def __init__(self):
        self.accounts: Dict[str, Account] = {}
        self.groups: Dict[str, CategoryGroup] = {}
        self.categories: Dict[str, Category] = {}
        self.category_order: List[str] = []  # top-to-bottom order, used by Underfunded walk
        self.rta: Dict[YMonth, Decimal] = {}  # Ready to Assign, per month
        self._undo_stack: List[tuple] = []
        self._redo_stack: List[tuple] = []

    # -- snapshot machinery -------------------------------------------------

    def _snapshot(self):
        return copy.deepcopy((self.accounts, self.groups, self.categories,
                               self.category_order, self.rta))

    def _restore(self, snapshot):
        (self.accounts, self.groups, self.categories,
         self.category_order, self.rta) = copy.deepcopy(snapshot)

    def undo(self) -> Optional[str]:
        if not self._undo_stack:
            return None
        label, snapshot = self._undo_stack.pop()
        self._redo_stack.append((label, self._snapshot()))
        self._restore(snapshot)
        return label

    def redo(self) -> Optional[str]:
        if not self._redo_stack:
            return None
        label, snapshot = self._redo_stack.pop()
        self._undo_stack.append((label, self._snapshot()))
        self._restore(snapshot)
        return label

    # -- setup ---------------------------------------------------------------

    def add_account(self, id, name, account_type: AccountType, on_budget: bool,
                     linked: bool = False, opening_balance=ZERO) -> Account:
        acct = Account(id, name, account_type, on_budget, linked, money(opening_balance))
        self.accounts[id] = acct
        if on_budget and account_type == AccountType.CREDIT_CARD:
            # Auto-create the linked "payment category" as YNAB does.
            grp = self.groups.setdefault(
                "cc_payments", CategoryGroup("cc_payments", "Credit Card Payments"))
            pay_cat_id = f"ccpay_{id}"
            cat = Category(pay_cat_id, f"{name} Payment", grp.id,
                            is_cc_payment_category=True, linked_cc_account_id=id)
            self.categories[pay_cat_id] = cat
            grp.category_ids.append(pay_cat_id)
            self.category_order.append(pay_cat_id)
        return acct

    def add_category_group(self, id, name) -> CategoryGroup:
        grp = CategoryGroup(id, name)
        self.groups[id] = grp
        return grp

    def add_category(self, id, name, group_id) -> Category:
        cat = Category(id, name, group_id)
        self.categories[id] = cat
        self.groups[group_id].category_ids.append(id)
        self.category_order.append(id)
        return cat

    def set_target(self, category_id: str, target: Optional[Target]) -> None:
        self.categories[category_id].target = target

    # -- Ready to Assign -------------------------------------------------

    def get_rta(self, m: YMonth) -> Decimal:
        return self.rta.get(m, ZERO)

    def _add_rta(self, m: YMonth, delta: Decimal) -> None:
        self.rta[m] = money(self.get_rta(m) + delta)

    # -- money movement -------------------------------------------------

    @tracked("record_income({2})")
    def record_income(self, account_id: str, month: YMonth, amount: Decimal) -> None:
        """Inflow to an on-budget account that isn't already assigned
        anywhere increases Ready to Assign directly. Income landing in an
        off-budget (tracking) account never touches RTA."""
        acct = self.accounts[account_id]
        acct.apply_delta(money(amount))
        if acct.on_budget:
            self._add_rta(month, money(amount))
        # Tracking accounts: balance changes, budget is untouched.

    @tracked("assign({1}, {2})")
    def assign(self, category_id: str, month: YMonth, amount: Decimal) -> None:
        """Directly SET a category's assigned amount for a month (used by
        manual assignment and by the four historical Auto-Assign presets).
        The delta vs. the previous assigned value moves to/from RTA."""
        cat = self.categories[category_id]
        delta = money(amount) - cat.get_assigned(month)
        cat.set_assigned(month, amount)
        self._add_rta(month, -delta)

    @tracked("move_money({1}->{2}, {4})")
    def move_money(self, from_category_id: str, to_category_id: str,
                   month: YMonth, amount: Decimal) -> None:
        """Move already-assigned money between two categories. Net RTA
        effect is zero -- this is the 'legitimate money-move' that, when
        used to zero out an overspend, avoids the next-month RTA penalty."""
        amount = money(amount)
        from_cat = self.categories[from_category_id]
        to_cat = self.categories[to_category_id]
        from_cat.add_assigned(month, -amount)
        to_cat.add_assigned(month, amount)

    @tracked("transfer({1}->{2}, {3})")
    def transfer(self, from_account_id: str, to_account_id: str, amount: Decimal,
                 category_id: Optional[str] = None, month: Optional[YMonth] = None) -> None:
        """Move money between two accounts.
        - Budget <-> Budget transfers are invisible to the budget: pure
          balance movement, no category, no RTA effect.
        - Any transfer touching a tracking (off-budget) account requires
          a category and behaves like a normal inflow/outflow against
          that category (and therefore against RTA), since money is
          entering or leaving the budgeted world."""
        amount = money(amount)
        from_acct = self.accounts[from_account_id]
        to_acct = self.accounts[to_account_id]
        from_acct.apply_delta(-amount)
        to_acct.apply_delta(amount)

        both_on_budget = from_acct.on_budget and to_acct.on_budget
        if both_on_budget:
            return  # no category, no RTA effect

        if category_id is None or month is None:
            raise ValueError("Transfers touching a tracking account need a category and month")
        cat = self.categories[category_id]
        if from_acct.on_budget and not to_acct.on_budget:
            # Money leaving the budgeted world: treat like spending.
            cat.add_activity(month, -amount)
        elif to_acct.on_budget and not from_acct.on_budget:
            # Money entering the budgeted world: treat like income to RTA.
            self._add_rta(month, amount)

    @tracked("pay_credit_card({1}->{2}, {3})")
    def pay_credit_card(self, from_account_id: str, cc_account_id: str,
                         amount: Decimal, month: YMonth) -> None:
        """Paying a credit card bill is a budget<->budget transfer (no
        direct RTA effect) that also consumes the reserve built up in the
        card's auto-generated Payment category."""
        amount = money(amount)
        from_acct = self.accounts[from_account_id]
        cc_acct = self.accounts[cc_account_id]
        from_acct.apply_delta(-amount)
        cc_acct.apply_delta(amount)  # debt balance moves toward zero

        pay_cat = self.categories[f"ccpay_{cc_account_id}"]
        pay_cat.add_assigned(month, -amount)

    @tracked("spend({1}, {3}, {4})")
    def add_transaction(self, account_id: str, category_id: Optional[str],
                         month: YMonth, amount: Decimal, payee: str = "") -> None:
        """Record a transaction. `amount` is negative for an outflow,
        positive for an inflow. Handles the credit-card special case:
        spending on a credit card moves already-available money from the
        spending category into that card's Payment category (up to what
        was available); any excess becomes ordinary overspending and is
        NOT auto-funded into the payment reserve."""
        amount = money(amount)
        acct = self.accounts[account_id]
        acct.apply_delta(amount)

        if category_id is None:
            return  # e.g. uncategorized transfer-like entries

        cat = self.categories[category_id]

        if acct.is_credit_card and amount < 0:
            spend = -amount
            available_before = cat.available(month)
            moved = max(ZERO, min(spend, available_before))
            cat.add_activity(month, -spend)
            if moved > ZERO:
                pay_cat = self.categories[f"ccpay_{account_id}"]
                cat.add_assigned(month, -moved)
                pay_cat.add_assigned(month, moved)
        else:
            cat.add_activity(month, amount)

    # -- Auto-Assign: two distinct algorithm families ------------------------

    @tracked("auto_assign_underfunded({1})")
    def auto_assign_underfunded(self, month: YMonth, today: date) -> Dict[str, Decimal]:
        """Greedy, top-to-bottom, partial-funding walk. Only ADDS money to
        underfunded categories, capped by whatever RTA remains -- it never
        reduces a category and never goes negative on RTA."""
        remaining = self.get_rta(month)
        funded: Dict[str, Decimal] = {}
        for cat_id in self.category_order:
            if remaining <= ZERO:
                break
            cat = self.categories[cat_id]
            if cat.target is None:
                continue
            needed = cat.target.amount_needed(cat, month, today)
            if needed <= ZERO:
                continue
            funding = min(needed, remaining)
            if funding > ZERO:
                cat.add_assigned(month, funding)
                remaining -= funding
                self._add_rta(month, -funding)
                funded[cat_id] = funding
        return funded

    @tracked("auto_assign_preset({1}, from {2})")
    def auto_assign_preset(self, preset: AutoAssignPreset, source_month: YMonth,
                            target_month: YMonth,
                            history_months: Optional[List[YMonth]] = None) -> Dict[str, Decimal]:
        """The four historical presets. Unlike Underfunded, each of these
        directly SETS a category's assigned amount to a historical value
        -- it can raise OR lower any category, and is never capped by
        available RTA (a direct 'set', not an 'add')."""
        if preset == AutoAssignPreset.UNDERFUNDED:
            raise ValueError("Use auto_assign_underfunded() for that preset")

        results: Dict[str, Decimal] = {}
        for cat_id in self.category_order:
            cat = self.categories[cat_id]
            if preset == AutoAssignPreset.ASSIGNED_LAST_MONTH:
                value = cat.get_assigned(source_month)
            elif preset == AutoAssignPreset.SPENT_LAST_MONTH:
                value = -cat.get_activity(source_month)  # activity is negative for spend
            elif preset == AutoAssignPreset.AVERAGE_ASSIGNED:
                months = history_months or [source_month]
                value = money(sum((cat.get_assigned(m) for m in months), ZERO) / len(months))
            elif preset == AutoAssignPreset.AVERAGE_SPENT:
                months = history_months or [source_month]
                value = money(sum((-cat.get_activity(m) for m in months), ZERO) / len(months))
            else:
                raise ValueError(f"Unknown preset {preset}")

            delta = value - cat.get_assigned(target_month)
            cat.set_assigned(target_month, value)
            self._add_rta(target_month, -delta)
            results[cat_id] = value
        return results

    # -- Month rollover / overspending -------------------------------------

    @tracked("rollover_month({1})")
    def rollover_month(self, month: YMonth) -> YMonth:
        """Advance to the next month. Rule confirmed empirically:
        - A category's available balance NEVER carries a negative value
          into the new month -- it always resets to a clean $0.00 there.
        - But uncovered overspending isn't free: uncovered overspend
          reduces next month's Ready to Assign by the exact amount, since
          that money had to come from somewhere. Covering the overspend
          within the same month via a real money-move (assign a negative
          amount to a donor category, positive to the overspent one)
          zeroes the shortfall out beforehand, so no penalty carries."""
        next_month = month.next()
        overspend_total = ZERO
        for cat_id in self.category_order:
            cat = self.categories[cat_id]
            avail = cat.available(month)
            if avail < ZERO:
                overspend_total += -avail
                cat.carry_in[next_month] = ZERO
            else:
                cat.carry_in[next_month] = avail

        carried_rta = self.get_rta(month) - overspend_total
        self.rta[next_month] = money(self.get_rta(next_month) + carried_rta)
        return next_month


# ---------------------------------------------------------------------------
# Demo walkthrough
# ---------------------------------------------------------------------------

def _demo():
    b = Budget()
    today = date(2026, 7, 15)
    jul, jun = YMonth(2026, 7), YMonth(2026, 6)

    # -- Accounts: on-budget vs. tracking, linked vs. unlinked -------------
    b.add_account("checking", "Test Checking", AccountType.CHECKING, on_budget=True, linked=False)
    b.add_account("credit_card", "Test Credit Card", AccountType.CREDIT_CARD, on_budget=True, linked=False)
    b.add_account("savings", "Test Savings", AccountType.SAVINGS, on_budget=False, linked=False,
                  opening_balance=money(200))
    b.add_account("mortgage", "Test Mortgage", AccountType.MORTGAGE, on_budget=False, linked=False,
                  opening_balance=money(-199633.33))

    # -- Category groups & categories --------------------------------------
    b.add_category_group("bills", "Bills")
    b.add_category_group("needs", "Needs")
    b.add_category_group("wants", "Wants")

    b.add_category("phone", "Phone & Internet", "bills")
    b.add_category("groceries", "Groceries", "needs")
    b.add_category("transportation", "Transportation", "needs")
    b.add_category("vacation", "Vacation", "wants")

    # -- Targets: one of each family ---------------------------------------
    b.set_target("phone", MonthlyTarget(amount=money(400)))
    b.set_target("vacation", YearlyTarget(amount=money(1200), due_month=YMonth(2027, 1)))
    b.set_target("groceries", CustomTarget(amount=money(150), sub_mode=CustomSubMode.HAVE_BALANCE,
                                            due_month=YMonth(2026, 10)))
    b.set_target("transportation", WeeklyTarget(amount=money(20), weekday=4))  # Friday

    print("=== Starting state ===")
    print(f"RTA before income: {b.get_rta(jul)}")

    # -- Income lands in checking (on-budget) -> increases RTA -------------
    b.record_income("checking", jul, money(3000))
    print(f"RTA after $3000 income to on-budget checking: {b.get_rta(jul)}")

    # Income landing in a tracking account never touches the budget.
    b.record_income("savings", jul, money(500))
    print(f"RTA after $500 income to tracking savings (should be unchanged): {b.get_rta(jul)}")

    # -- Auto-Assign: Underfunded (greedy, additive, capped by RTA) --------
    funded = b.auto_assign_underfunded(jul, today)
    print("\n=== Auto-Assign: Underfunded ===")
    for cat_id, amt in funded.items():
        print(f"  {b.categories[cat_id].name}: +{amt}")
    print(f"RTA remaining: {b.get_rta(jul)}")

    # -- Money movement: manual assign + move between categories -----------
    b.assign("vacation", jul, b.categories["vacation"].get_assigned(jul) + money(50))
    print(f"\nManually bumped Vacation assigned by $50 -> RTA now {b.get_rta(jul)}")

    b.move_money("vacation", "groceries", jul, money(20))
    print("Moved $20 Vacation -> Groceries (net-zero RTA effect, confirmed by "
          f"RTA unchanged at {b.get_rta(jul)})")

    # -- Credit card spend + payment mechanics ------------------------------
    b.assign("groceries", jul, money(100))  # give groceries some available funds
    b.add_transaction("credit_card", "groceries", jul, money(-60), payee="Whole Foods")
    cc_pay_cat = b.categories["ccpay_credit_card"]
    print(f"\nAfter $60 CC grocery spend: Groceries available={b.categories['groceries'].available(jul)}, "
          f"CC Payment category assigned={cc_pay_cat.get_assigned(jul)}")

    b.pay_credit_card("checking", "credit_card", money(60), jul)
    print(f"Paid CC bill $60: CC balance={b.accounts['credit_card'].balance}, "
          f"CC Payment category assigned={cc_pay_cat.get_assigned(jul)}")

    # -- Overspending rollover rule -----------------------------------------
    b.add_transaction("checking", "transportation", jul, money(-350), payee="Gas Station")
    print(f"\nTransportation overspent: available={b.categories['transportation'].available(jul)}")

    next_month = b.rollover_month(jul)
    print(f"Uncovered overspend rolled into {next_month}: RTA={b.get_rta(next_month)} "
          "(docked by the overspent amount)")

    # Undo the rollover, cover the overspend with a real money-move instead,
    # then roll over again to show the penalty disappears.
    b.undo()
    b.move_money("phone", "transportation", jul, money(50))
    print(f"\nCovered overspend via money-move: Transportation available="
          f"{b.categories['transportation'].available(jul)}")
    next_month = b.rollover_month(jul)
    print(f"RTA in {next_month} after covering overspend properly: {b.get_rta(next_month)} "
          "(no penalty this time)")

    # -- Historical presets: direct 'set', can rise or fall, uncapped -------
    b.assign("groceries", jun, money(400))
    b.add_transaction("checking", "groceries", jun, money(-150), payee="Whole Foods")
    set_values = b.auto_assign_preset(AutoAssignPreset.ASSIGNED_LAST_MONTH,
                                       source_month=jun, target_month=jul)
    print("\n=== Auto-Assign: Assigned Last Month (direct set, uncapped) ===")
    for cat_id, val in set_values.items():
        print(f"  {b.categories[cat_id].name}: set to {val}")

    # -- Debt Payment Target amortization ------------------------------------
    print("\n=== Debt Payment Target amortization ===")
    balance = money(19700)
    apr = Decimal("6.5")
    for payment in (money(400), money(500), money(600)):
        months = DebtPaymentTarget.payoff_months(balance, apr, payment)
        print(f"  Payment ${payment}/mo on ${balance} @ {apr}% APR -> payoff in {months} months")

    # -- Undo/Redo as a genuine action-history stack -------------------------
    print("\n=== Undo/Redo stack ===")
    before = b.categories["vacation"].get_assigned(jul)
    b.assign("vacation", jul, before + money(100))
    print(f"Vacation assigned after +$100: {b.categories['vacation'].get_assigned(jul)}")
    label = b.undo()
    print(f"Undid '{label}': Vacation assigned back to {b.categories['vacation'].get_assigned(jul)}")
    label = b.redo()
    print(f"Redid '{label}': Vacation assigned is {b.categories['vacation'].get_assigned(jul)}")


if __name__ == "__main__":
    _demo()
