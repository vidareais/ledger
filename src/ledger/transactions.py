"""Immutable transaction ledger entries (DESIGN.md section 8)."""

import datetime
from dataclasses import dataclass
from decimal import Decimal

RTA_INFLOW = "inflow:ready-to-assign"
"""Sentinel category id for inflows that fund Ready to Assign (section 3)."""


@dataclass(frozen=True)
class SplitLine:
    category_id: str
    amount: Decimal
    memo: str = ""


@dataclass(frozen=True)
class Transaction:
    id: str
    account_id: str
    date: datetime.date
    amount: Decimal
    payee_id: str | None = None
    category_id: str | None = None
    splits: tuple[SplitLine, ...] = ()
    transfer_id: str | None = None
    memo: str = ""

    def lines(self) -> tuple[tuple[str, Decimal], ...]:
        """The (category_id, amount) allocations this transaction fans out into."""
        if self.splits:
            return tuple((split.category_id, split.amount) for split in self.splits)
        if self.category_id is not None:
            return ((self.category_id, self.amount),)
        return ()
