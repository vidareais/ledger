"""The Plan aggregate: entity stores, the transaction ledger, and derived math.

Transactions and assigned amounts are the stored facts. Balances, activity,
carry-in, Available, and Ready to Assign are all derived from them, so the
rollover rules of DESIGN.md sections 3-4 fall out of the formulas rather
than being applied by an explicit month-close step.
"""

import datetime
import itertools
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum, auto

from ledger.accounts import CLASS_BY_TYPE, Account, AccountClass, AccountType
from ledger.categories import Category, CategoryGroup
from ledger.errors import LedgerError, SplitMismatchError, UnknownEntityError
from ledger.money import ZERO, Amount, money
from ledger.month import YMonth
from ledger.payees import Payee
from ledger.schedules import Frequency, ScheduledTransaction, next_occurrence
from ledger.targets import Target
from ledger.transactions import RTA_INFLOW, ClearedStatus, SplitLine, Transaction

PAYMENT_GROUP_ID = "credit-card-payments"


class AutoAssignPreset(Enum):
    ASSIGNED_LAST_MONTH = auto()
    SPENT_LAST_MONTH = auto()
    AVERAGE_ASSIGNED = auto()
    AVERAGE_SPENT = auto()


@dataclass(frozen=True)
class LoanPayment:
    """How a recorded loan payment decomposed (section 5.4)."""

    payment: Decimal
    principal: Decimal
    interest: Decimal
    transfer: tuple[Transaction, Transaction]
    interest_charge: Transaction | None


class Plan:
    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}
        self.groups: dict[str, CategoryGroup] = {}
        self.group_order: list[str] = []
        self.categories: dict[str, Category] = {}
        self.payees: dict[str, Payee] = {}
        self.schedules: dict[str, ScheduledTransaction] = {}
        self._transactions: dict[str, Transaction] = {}
        self._txn_order: list[str] = []
        self._assigned: dict[tuple[str, YMonth], Decimal] = {}
        self._snoozed: set[tuple[str, YMonth]] = set()
        self._id_counter = itertools.count(1)

    # -- entity setup -------------------------------------------------------

    def add_account(
        self,
        account_id: str,
        name: str,
        account_type: AccountType,
        *,
        linked: bool = False,
        paired_category_id: str | None = None,
        apr_percent: Amount | None = None,
        opening_balance: Amount = 0,
        opening_date: datetime.date | None = None,
    ) -> Account:
        if account_id in self.accounts:
            raise LedgerError(f"duplicate account id {account_id!r}")
        account_class = CLASS_BY_TYPE[account_type]
        if account_class is AccountClass.LOANS:
            if paired_category_id is None:
                raise LedgerError("loan accounts must be paired with a category")
            if apr_percent is None:
                raise LedgerError("loan accounts need an interest rate (apr_percent)")
        if paired_category_id is not None:
            self._require_category(paired_category_id)
        account = Account(
            account_id,
            name,
            account_type,
            linked=linked,
            paired_category_id=paired_category_id,
            apr_percent=None if apr_percent is None else Decimal(str(apr_percent)),
        )
        self.accounts[account_id] = account
        if account_class is AccountClass.CREDIT:
            self._create_payment_category(account)
        self._record_opening_balance(account, money(opening_balance), opening_date)
        return account

    def add_category_group(self, group_id: str, name: str) -> CategoryGroup:
        if group_id in self.groups:
            raise LedgerError(f"duplicate group id {group_id!r}")
        group = CategoryGroup(group_id, name)
        self.groups[group_id] = group
        self.group_order.append(group_id)
        return group

    def add_category(self, category_id: str, name: str, group_id: str) -> Category:
        if category_id in self.categories:
            raise LedgerError(f"duplicate category id {category_id!r}")
        group = self.groups.get(group_id)
        if group is None:
            raise UnknownEntityError(f"unknown group {group_id!r}")
        category = Category(category_id, name, group_id)
        self.categories[category_id] = category
        group.category_ids.append(category_id)
        return category

    def set_target(self, category_id: str, target: Target | None) -> None:
        self._require_category(category_id).target = target

    def snooze(self, category_id: str, month: YMonth) -> None:
        self._require_category(category_id)
        self._snoozed.add((category_id, month))

    def unsnooze(self, category_id: str, month: YMonth) -> None:
        self._snoozed.discard((category_id, month))

    def display_order(self) -> list[str]:
        """Category ids in display order: group position, then position in group.

        This order is load-bearing: it is the only priority the Underfunded
        walk uses (section 7.1).
        """
        return [
            category_id
            for group_id in self.group_order
            for category_id in self.groups[group_id].category_ids
        ]

    def _create_payment_category(self, account: Account) -> None:
        if PAYMENT_GROUP_ID not in self.groups:
            self.add_category_group(PAYMENT_GROUP_ID, "Credit Card Payments")
        category = Category(
            f"payment:{account.id}",
            f"{account.name} Payment",
            PAYMENT_GROUP_ID,
            payment_account_id=account.id,
        )
        self.categories[category.id] = category
        self.groups[PAYMENT_GROUP_ID].category_ids.append(category.id)

    def _record_opening_balance(
        self, account: Account, opening: Decimal, opening_date: datetime.date | None
    ) -> None:
        if opening == ZERO:
            return
        if opening_date is None:
            raise LedgerError("opening_balance requires opening_date")
        is_cash = account.account_class is AccountClass.CASH
        self._record(
            Transaction(
                self._new_id("txn"),
                account.id,
                opening_date,
                opening,
                payee_id=self._payee_by_name("Starting Balance", structural=True).id,
                category_id=RTA_INFLOW if is_cash else None,
            )
        )

    # -- ledger writes ------------------------------------------------------

    def add_transaction(
        self,
        account_id: str,
        when: datetime.date,
        amount: Amount,
        *,
        category_id: str | None = None,
        splits: Iterable[SplitLine] = (),
        payee: str | None = None,
        cleared: bool = False,
        memo: str = "",
    ) -> Transaction:
        account = self._require_account(account_id)
        value = money(amount)
        split_lines = tuple(
            SplitLine(s.category_id, money(s.amount), s.memo) for s in splits
        )
        self._validate_categorization(account, category_id, split_lines)
        if split_lines:
            total = money(sum((line.amount for line in split_lines), ZERO))
            if total != value:
                raise SplitMismatchError(
                    f"splits sum to {total}, transaction amount is {value}"
                )
        payee_id = self._payee_by_name(payee).id if payee is not None else None
        txn = Transaction(
            self._new_id("txn"),
            account_id,
            when,
            value,
            payee_id=payee_id,
            category_id=category_id,
            splits=split_lines,
            status=ClearedStatus.CLEARED if cleared else ClearedStatus.UNCLEARED,
            memo=memo,
        )
        self._record(txn)
        return txn

    def add_transfer(
        self,
        from_account_id: str,
        to_account_id: str,
        when: datetime.date,
        amount: Amount,
        *,
        category_id: str | None = None,
        memo: str = "",
    ) -> tuple[Transaction, Transaction]:
        """Linked pair of transactions with structural payees (section 5.2)."""
        source = self._require_account(from_account_id)
        destination = self._require_account(to_account_id)
        if source.id == destination.id:
            raise LedgerError("cannot transfer an account to itself")
        value = money(amount)
        if value <= ZERO:
            raise LedgerError("transfer amount must be positive")
        out_category, in_category = self._transfer_categories(
            source, destination, category_id
        )
        transfer_id = self._new_id("xfer")
        txn_out = Transaction(
            self._new_id("txn"),
            source.id,
            when,
            -value,
            payee_id=self._payee_by_name(
                f"Transfer: {destination.name}", structural=True
            ).id,
            category_id=out_category,
            transfer_id=transfer_id,
            memo=memo,
        )
        txn_in = Transaction(
            self._new_id("txn"),
            destination.id,
            when,
            value,
            payee_id=self._payee_by_name(
                f"Transfer: {source.name}", structural=True
            ).id,
            category_id=in_category,
            transfer_id=transfer_id,
            memo=memo,
        )
        self._record(txn_out)
        self._record(txn_in)
        return txn_out, txn_in

    def _transfer_categories(
        self, source: Account, destination: Account, category_id: str | None
    ) -> tuple[str | None, str | None]:
        if source.on_budget and destination.on_budget:
            if category_id is not None:
                raise LedgerError("transfers between budget accounts take no category")
            return None, None
        if category_id is None:
            raise LedgerError(
                "transfers touching an off-budget account require a category"
            )
        self._validate_category_ref(category_id, allow_rta=True)
        out_category = category_id if source.on_budget else None
        in_category = category_id if destination.on_budget else None
        return out_category, in_category

    # -- assignment writes --------------------------------------------------

    def assign(self, category_id: str, month: YMonth, amount: Amount) -> None:
        """Directly set a category's assigned amount for a month (section 5.1)."""
        self._require_category(category_id)
        self._assigned[(category_id, month)] = money(amount)

    def move_money(
        self, from_category_id: str, to_category_id: str, month: YMonth, amount: Amount
    ) -> None:
        """Reallocate assigned money between categories; net-zero RTA effect."""
        value = money(amount)
        self.assign(
            from_category_id, month, self.assigned_for(from_category_id, month) - value
        )
        self.assign(
            to_category_id, month, self.assigned_for(to_category_id, month) + value
        )

    def apply_assignments(self, month: YMonth, values: Mapping[str, Amount]) -> None:
        """Atomic batch set: validate every category before writing any value."""
        normalized = {cid: money(value) for cid, value in values.items()}
        for category_id in normalized:
            self._require_category(category_id)
        for category_id, value in normalized.items():
            self._assigned[(category_id, month)] = value

    # -- derived queries ----------------------------------------------------

    def assigned_for(self, category_id: str, month: YMonth) -> Decimal:
        return self._assigned.get((category_id, month), ZERO)

    def activity(self, category_id: str, month: YMonth) -> Decimal:
        category = self._require_category(category_id)
        if category.payment_account_id is not None:
            return self._payment_activity(category.payment_account_id, month)
        total = ZERO
        for txn in self._month_txns(month):
            for line_category, line_amount in txn.lines():
                if line_category == category_id:
                    total += line_amount
        return money(total)

    def carry_in(self, category_id: str, month: YMonth) -> Decimal:
        """Section 4: prior Available carries forward only when positive."""
        earliest = self._earliest_month()
        if earliest is None or month <= earliest:
            return ZERO
        return max(ZERO, self.available(category_id, month.prev()))

    def available(self, category_id: str, month: YMonth) -> Decimal:
        return money(
            self.carry_in(category_id, month)
            + self.assigned_for(category_id, month)
            + self.activity(category_id, month)
        )

    def rta(self, month: YMonth) -> Decimal:
        """Section 3: cumulative inflows-to-RTA dated through `month`, minus
        every assignment in any month, minus uncovered overspending from
        months before `month` (section 4.2)."""
        inflows = ZERO
        for txn in self._ordered_txns():
            if YMonth.of(txn.date) > month:
                continue
            for line_category, line_amount in txn.lines():
                if line_category == RTA_INFLOW:
                    inflows += line_amount
        assigned_total = sum(self._assigned.values(), ZERO)
        return money(inflows - assigned_total - self._overspend_docked(month))

    def account_balance(self, account_id: str) -> Decimal:
        self._require_account(account_id)
        return money(
            sum(
                (t.amount for t in self._ordered_txns() if t.account_id == account_id),
                ZERO,
            )
        )

    def net_worth(self) -> Decimal:
        """Section 2: sum of every account balance, tracking included."""
        return money(
            sum(
                (self.account_balance(account_id) for account_id in self.accounts), ZERO
            )
        )

    def transactions(self) -> tuple[Transaction, ...]:
        return tuple(self._ordered_txns())

    # -- transaction status and reconciliation (section 8.3) ----------------

    def get_transaction(self, txn_id: str) -> Transaction:
        txn = self._transactions.get(txn_id)
        if txn is None:
            raise UnknownEntityError(f"unknown transaction {txn_id!r}")
        return txn

    def set_cleared(self, txn_id: str, *, cleared: bool) -> Transaction:
        txn = self.get_transaction(txn_id)
        if txn.status is ClearedStatus.RECONCILED:
            raise LedgerError("reconciled transactions are locked")
        status = ClearedStatus.CLEARED if cleared else ClearedStatus.UNCLEARED
        updated = replace(txn, status=status)
        self._transactions[txn_id] = updated
        return updated

    def delete_transaction(self, txn_id: str) -> None:
        """Deleting one leg of a transfer deletes both; reconciled
        transactions are locked and refuse deletion."""
        txn = self.get_transaction(txn_id)
        doomed = [txn]
        if txn.transfer_id is not None:
            doomed = [
                t
                for t in self._transactions.values()
                if t.transfer_id == txn.transfer_id
            ]
        for t in doomed:
            if t.status is ClearedStatus.RECONCILED:
                raise LedgerError("reconciled transactions are locked")
        for t in doomed:
            del self._transactions[t.id]
            self._txn_order.remove(t.id)

    def cleared_balance(self, account_id: str) -> Decimal:
        self._require_account(account_id)
        return money(
            sum(
                (
                    t.amount
                    for t in self._ordered_txns()
                    if t.account_id == account_id
                    and t.status is not ClearedStatus.UNCLEARED
                ),
                ZERO,
            )
        )

    def reconcile(
        self, account_id: str, when: datetime.date, actual_balance: Amount
    ) -> Transaction | None:
        """Trust boundary: compare the entered real-world balance against the
        cleared balance, insert a cleared adjustment for any difference
        (returned, else None), then lock every cleared transaction on the
        account. Cash-account adjustments enter through Ready to Assign,
        mirroring opening balances; credit and tracking adjustments are
        uncategorized (section 8.3)."""
        account = self._require_account(account_id)
        difference = money(actual_balance) - self.cleared_balance(account_id)
        adjustment: Transaction | None = None
        if difference != ZERO:
            is_cash = account.account_class is AccountClass.CASH
            adjustment = Transaction(
                self._new_id("txn"),
                account_id,
                when,
                difference,
                payee_id=self._payee_by_name(
                    "Reconciliation Balance Adjustment", structural=True
                ).id,
                category_id=RTA_INFLOW if is_cash else None,
                status=ClearedStatus.CLEARED,
            )
            self._record(adjustment)
        for txn in list(self._transactions.values()):
            if txn.account_id == account_id and txn.status is ClearedStatus.CLEARED:
                self._transactions[txn.id] = replace(
                    txn, status=ClearedStatus.RECONCILED
                )
        account.last_reconciled = when
        return adjustment

    # -- loan payments (section 5.4) ----------------------------------------

    def record_loan_payment(
        self,
        from_account_id: str,
        loan_account_id: str,
        when: datetime.date,
        amount: Amount,
    ) -> LoanPayment:
        """A loan payment is a transfer categorized to the loan's paired
        category, decomposed against the stored rate: the period's accrued
        interest is charged to the loan first, so the balance improves by
        the principal portion only — never 1:1 with the payment."""
        loan = self._require_account(loan_account_id)
        if loan.account_class is not AccountClass.LOANS:
            raise LedgerError("record_loan_payment requires a loan account")
        if loan.apr_percent is None:
            raise LedgerError("loan account has no interest rate")
        payment = money(amount)
        if payment <= ZERO:
            raise LedgerError("payment must be positive")
        balance_owed = -self.account_balance(loan_account_id)
        interest = max(
            ZERO, money(balance_owed * loan.apr_percent / Decimal(100) / Decimal(12))
        )
        interest_charge: Transaction | None = None
        if interest > ZERO:
            interest_charge = Transaction(
                self._new_id("txn"),
                loan_account_id,
                when,
                -interest,
                payee_id=self._payee_by_name("Interest", structural=True).id,
            )
            self._record(interest_charge)
        legs = self.add_transfer(
            from_account_id,
            loan_account_id,
            when,
            payment,
            category_id=loan.paired_category_id,
        )
        return LoanPayment(
            payment=payment,
            principal=money(payment - interest),
            interest=interest,
            transfer=legs,
            interest_charge=interest_charge,
        )

    # -- scheduled transactions (section 8.2) -------------------------------

    def add_schedule(
        self,
        account_id: str,
        first_date: datetime.date,
        amount: Amount,
        frequency: Frequency,
        *,
        category_id: str | None = None,
        payee: str | None = None,
        memo: str = "",
        today: datetime.date | None = None,
    ) -> tuple[ScheduledTransaction, list[Transaction]]:
        """Register a repeating transaction. Passing `today` reproduces the
        observed creation behavior: any occurrence already due (the current
        due date included) materializes immediately as a real, unapproved
        transaction, and the schedule advances to the next future one."""
        account = self._require_account(account_id)
        self._validate_categorization(account, category_id, ())
        payee_id = self._payee_by_name(payee).id if payee is not None else None
        schedule = ScheduledTransaction(
            self._new_id("sched"),
            account_id,
            first_date,
            money(amount),
            frequency,
            first_date.day,
            payee_id=payee_id,
            category_id=category_id,
            memo=memo,
        )
        self.schedules[schedule.id] = schedule
        created = [] if today is None else self._materialize_schedule(schedule, today)
        return schedule, created

    def delete_schedule(self, schedule_id: str) -> None:
        if schedule_id not in self.schedules:
            raise UnknownEntityError(f"unknown schedule {schedule_id!r}")
        del self.schedules[schedule_id]

    def materialize_due(self, today: datetime.date) -> list[Transaction]:
        """Every scheduled occurrence whose date has arrived becomes a real
        transaction awaiting approval; each schedule advances past `today`."""
        created: list[Transaction] = []
        for schedule in self.schedules.values():
            created.extend(self._materialize_schedule(schedule, today))
        return created

    def _materialize_schedule(
        self, schedule: ScheduledTransaction, today: datetime.date
    ) -> list[Transaction]:
        created: list[Transaction] = []
        while schedule.next_date <= today:
            txn = Transaction(
                self._new_id("txn"),
                schedule.account_id,
                schedule.next_date,
                schedule.amount,
                payee_id=schedule.payee_id,
                category_id=schedule.category_id,
                approved=False,
                memo=schedule.memo,
            )
            self._record(txn)
            created.append(txn)
            schedule.next_date = next_occurrence(
                schedule.next_date, schedule.frequency, schedule.anchor_day
            )
        return created

    def approve(self, txn_id: str) -> Transaction:
        updated = replace(self.get_transaction(txn_id), approved=True)
        self._transactions[txn_id] = updated
        return updated

    def pending_approval(self) -> tuple[Transaction, ...]:
        return tuple(t for t in self._ordered_txns() if not t.approved)

    # -- payees (section 8.4) -----------------------------------------------

    def rename_payee(self, payee_id: str, name: str) -> Payee:
        payee = self._require_payee(payee_id)
        if payee.structural:
            raise LedgerError("structural payees cannot be renamed")
        payee.name = name
        return payee

    def merge_payees(self, losing_payee_id: str, surviving_payee_id: str) -> int:
        """Foreign-key rewrite plus delete, not a blended entity: every
        transaction and schedule tagged with the losing payee is repointed
        at the surviving one (reconciled rows included — the merge rewrites
        metadata, not financial history), then the losing payee record is
        retired. Returns the number of repointed references."""
        losing = self._require_payee(losing_payee_id)
        surviving = self._require_payee(surviving_payee_id)
        if losing.id == surviving.id:
            raise LedgerError("cannot merge a payee into itself")
        if losing.structural or surviving.structural:
            raise LedgerError("structural payees cannot be merged")
        repointed = 0
        for txn in list(self._transactions.values()):
            if txn.payee_id == losing.id:
                self._transactions[txn.id] = replace(txn, payee_id=surviving.id)
                repointed += 1
        for schedule in self.schedules.values():
            if schedule.payee_id == losing.id:
                schedule.payee_id = surviving.id
                repointed += 1
        del self.payees[losing.id]
        return repointed

    # -- auto-assign (section 7) --------------------------------------------

    def preview_underfunded(
        self, month: YMonth, today: datetime.date
    ) -> dict[str, Decimal]:
        """Greedy top-to-bottom walk: proposed final assigned values, additive,
        capped by RTA, strictly zero for everything past the partial-fill
        boundary. Apply with apply_assignments()."""
        pool = self.rta(month)
        proposal: dict[str, Decimal] = {}
        for category_id in self.display_order():
            if pool <= ZERO:
                break
            category = self.categories[category_id]
            if category.target is None or (category_id, month) in self._snoozed:
                continue
            needed = category.target.amount_needed(self, category_id, month, today)
            if needed <= ZERO:
                continue
            grant = min(needed, pool)
            proposal[category_id] = money(self.assigned_for(category_id, month) + grant)
            pool = money(pool - grant)
        return proposal

    def preview_preset(
        self,
        preset: AutoAssignPreset,
        month: YMonth,
        history: list[YMonth] | None = None,
    ) -> dict[str, Decimal]:
        """Historical presets: a direct set for every category, raising or
        lowering freely, never capped by RTA. Apply with apply_assignments()."""
        months = history if history is not None else self._history_months(month)
        return {
            category_id: self._preset_value(preset, category_id, month, months)
            for category_id in self.display_order()
        }

    def _preset_value(
        self,
        preset: AutoAssignPreset,
        category_id: str,
        month: YMonth,
        months: list[YMonth],
    ) -> Decimal:
        if not months:
            return ZERO
        match preset:
            case AutoAssignPreset.ASSIGNED_LAST_MONTH:
                return self.assigned_for(category_id, month.prev())
            case AutoAssignPreset.SPENT_LAST_MONTH:
                return max(ZERO, -self.activity(category_id, month.prev()))
            case AutoAssignPreset.AVERAGE_ASSIGNED:
                total = sum((self.assigned_for(category_id, m) for m in months), ZERO)
                return money(total / len(months))
            case AutoAssignPreset.AVERAGE_SPENT:
                total = sum(
                    (max(ZERO, -self.activity(category_id, m)) for m in months), ZERO
                )
                return money(total / len(months))

    def _history_months(self, month: YMonth) -> list[YMonth]:
        earliest = self._earliest_month()
        months: list[YMonth] = []
        if earliest is None:
            return months
        current = earliest
        while current < month:
            months.append(current)
            current = current.next()
        return months

    # -- credit card mechanics (section 5.3) --------------------------------

    def _payment_activity(self, account_id: str, month: YMonth) -> Decimal:
        funded = self._funded_spending(month).get(account_id, ZERO)
        payments = ZERO
        for txn in self._month_txns(month):
            if (
                txn.account_id == account_id
                and txn.transfer_id is not None
                and txn.amount > ZERO
            ):
                payments += txn.amount
        return money(funded - payments)

    def _funded_spending(self, month: YMonth) -> dict[str, Decimal]:
        """Budgeted dollars earmarked into each credit account's payment
        category: per spending category, walk its lines in ledger order and
        move each credit spend up to the running available balance."""
        by_category: dict[str, list[tuple[str, Decimal]]] = {}
        for txn in self._month_txns(month):
            for line_category, line_amount in txn.lines():
                if line_category != RTA_INFLOW:
                    by_category.setdefault(line_category, []).append(
                        (txn.account_id, line_amount)
                    )
        funded: dict[str, Decimal] = {}
        for category_id, entries in by_category.items():
            if self.categories[category_id].payment_account_id is not None:
                continue
            running = self.carry_in(category_id, month) + self.assigned_for(
                category_id, month
            )
            for account_id, line_amount in entries:
                account = self.accounts[account_id]
                is_credit = account.account_class is AccountClass.CREDIT
                if is_credit and line_amount < ZERO:
                    moved = min(-line_amount, max(ZERO, running))
                    funded[account_id] = money(funded.get(account_id, ZERO) + moved)
                running += line_amount
        return funded

    # -- internals ----------------------------------------------------------

    def _overspend_docked(self, month: YMonth) -> Decimal:
        """Cumulative uncovered overspending from months before `month`."""
        earliest = self._earliest_month()
        if earliest is None:
            return ZERO
        docked = ZERO
        current = earliest
        while current < month:
            for category_id in self.categories:
                shortfall = -self.available(category_id, current)
                if shortfall > ZERO:
                    docked += shortfall
            current = current.next()
        return money(docked)

    def _earliest_month(self) -> YMonth | None:
        months = [YMonth.of(t.date) for t in self._transactions.values()]
        months.extend(month for _, month in self._assigned)
        return min(months) if months else None

    def _ordered_txns(self) -> list[Transaction]:
        txns = [self._transactions[txn_id] for txn_id in self._txn_order]
        return sorted(txns, key=lambda t: t.date)

    def _month_txns(self, month: YMonth) -> list[Transaction]:
        return [t for t in self._ordered_txns() if YMonth.of(t.date) == month]

    def _validate_categorization(
        self,
        account: Account,
        category_id: str | None,
        split_lines: tuple[SplitLine, ...],
    ) -> None:
        if split_lines and category_id is not None:
            raise LedgerError("a split transaction cannot also have a category")
        if not account.on_budget:
            if category_id is not None or split_lines:
                raise LedgerError("off-budget account transactions take no category")
            return
        if category_id is None and not split_lines:
            raise LedgerError("budget-account transactions require a category")
        self._validate_category_ref(category_id, allow_rta=True)
        for line in split_lines:
            self._validate_category_ref(line.category_id, allow_rta=False)

    def _validate_category_ref(
        self, category_id: str | None, *, allow_rta: bool
    ) -> None:
        if category_id is None:
            return
        if category_id == RTA_INFLOW:
            if not allow_rta:
                raise LedgerError("Ready to Assign cannot be used in a split")
            return
        self._require_category(category_id)

    def _payee_by_name(self, name: str, *, structural: bool = False) -> Payee:
        for payee in self.payees.values():
            if payee.name == name and payee.structural == structural:
                return payee
        payee = Payee(self._new_id("payee"), name, structural)
        self.payees[payee.id] = payee
        return payee

    def _require_payee(self, payee_id: str) -> Payee:
        payee = self.payees.get(payee_id)
        if payee is None:
            raise UnknownEntityError(f"unknown payee {payee_id!r}")
        return payee

    def _require_account(self, account_id: str) -> Account:
        account = self.accounts.get(account_id)
        if account is None:
            raise UnknownEntityError(f"unknown account {account_id!r}")
        return account

    def _require_category(self, category_id: str) -> Category:
        category = self.categories.get(category_id)
        if category is None:
            raise UnknownEntityError(f"unknown category {category_id!r}")
        return category

    def _record(self, txn: Transaction) -> None:
        self._transactions[txn.id] = txn
        self._txn_order.append(txn.id)

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._id_counter)}"
