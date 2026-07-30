"""Account taxonomy (DESIGN.md section 2)."""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto


class AccountType(Enum):
    CHECKING = auto()
    SAVINGS = auto()
    CASH = auto()
    CREDIT_CARD = auto()
    LINE_OF_CREDIT = auto()
    MORTGAGE = auto()
    AUTO_LOAN = auto()
    STUDENT_LOAN = auto()
    PERSONAL_LOAN = auto()
    MEDICAL_DEBT = auto()
    OTHER_DEBT = auto()
    INVESTMENT = auto()
    OTHER_ASSET = auto()
    OTHER_LIABILITY = auto()


class AccountClass(Enum):
    CASH = auto()
    CREDIT = auto()
    LOANS = auto()
    TRACKING = auto()


CLASS_BY_TYPE: dict[AccountType, AccountClass] = {
    AccountType.CHECKING: AccountClass.CASH,
    AccountType.SAVINGS: AccountClass.CASH,
    AccountType.CASH: AccountClass.CASH,
    AccountType.CREDIT_CARD: AccountClass.CREDIT,
    AccountType.LINE_OF_CREDIT: AccountClass.CREDIT,
    AccountType.MORTGAGE: AccountClass.LOANS,
    AccountType.AUTO_LOAN: AccountClass.LOANS,
    AccountType.STUDENT_LOAN: AccountClass.LOANS,
    AccountType.PERSONAL_LOAN: AccountClass.LOANS,
    AccountType.MEDICAL_DEBT: AccountClass.LOANS,
    AccountType.OTHER_DEBT: AccountClass.LOANS,
    AccountType.INVESTMENT: AccountClass.TRACKING,
    AccountType.OTHER_ASSET: AccountClass.TRACKING,
    AccountType.OTHER_LIABILITY: AccountClass.TRACKING,
}


@dataclass
class Account:
    id: str
    name: str
    account_type: AccountType
    linked: bool = False
    paired_category_id: str | None = None
    apr_percent: Decimal | None = None
    note: str = ""
    closed: bool = False
    last_reconciled: datetime.date | None = None

    @property
    def account_class(self) -> AccountClass:
        return CLASS_BY_TYPE[self.account_type]

    @property
    def on_budget(self) -> bool:
        """Only CASH and CREDIT accounts hold budgeted, categorizable money;
        LOANS and TRACKING sit outside the partition (sections 2 and 5.4)."""
        return self.account_class in (AccountClass.CASH, AccountClass.CREDIT)
