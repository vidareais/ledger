"""Scheduled (recurring) transactions (DESIGN.md section 8.2)."""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from ledger.month import YMonth
from ledger.transactions import FlagColor


class Frequency(Enum):
    WEEKLY = auto()
    EVERY_OTHER_WEEK = auto()
    MONTHLY = auto()
    YEARLY = auto()


@dataclass
class ScheduledTransaction:
    id: str
    account_id: str
    next_date: datetime.date
    amount: Decimal
    frequency: Frequency
    anchor_day: int  # day-of-month of the first occurrence; prevents drift
    payee_id: str | None = None
    category_id: str | None = None
    flag_color: FlagColor | None = None
    memo: str = ""


def next_occurrence(
    current: datetime.date, frequency: Frequency, anchor_day: int
) -> datetime.date:
    """The occurrence after `current`. Month-based frequencies clamp to the
    month's last day but spring back to the anchor day (Jan 31 -> Feb 28 ->
    Mar 31)."""
    if frequency is Frequency.WEEKLY:
        return current + datetime.timedelta(days=7)
    if frequency is Frequency.EVERY_OTHER_WEEK:
        return current + datetime.timedelta(days=14)
    if frequency is Frequency.MONTHLY:
        month = YMonth.of(current).next()
    else:
        month = YMonth(current.year + 1, current.month)
    return datetime.date(month.year, month.month, min(anchor_day, month.last_day()))
