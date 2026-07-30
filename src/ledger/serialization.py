"""Entity <-> dict codecs for plan persistence.

Only stored facts are encoded; every derived figure (balances, Available,
RTA, Age of Money) is recomputed after loading. Amounts travel as exact
decimal strings, dates as ISO strings, enums by name.
"""

import datetime
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from ledger.accounts import Account, AccountType
from ledger.categories import Category, CategoryGroup
from ledger.errors import PersistenceError
from ledger.month import YMonth
from ledger.payees import Payee
from ledger.schedules import Frequency, ScheduledTransaction
from ledger.targets import (
    CustomSubMode,
    CustomTarget,
    DebtPaymentTarget,
    MonthlyTarget,
    Target,
    WeeklyTarget,
    YearlyTarget,
)
from ledger.transactions import ClearedStatus, SplitLine, Transaction

FORMAT = "ledger-plan"
VERSION = 1


def month_key(month: YMonth) -> str:
    return str(month)


def parse_month(value: str) -> YMonth:
    year, _, month = value.partition("-")
    return YMonth(int(year), int(month))


def _optional_date(value: str | None) -> datetime.date | None:
    return None if value is None else datetime.date.fromisoformat(value)


def account_to_dict(account: Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "name": account.name,
        "type": account.account_type.name,
        "linked": account.linked,
        "paired_category_id": account.paired_category_id,
        "apr_percent": None
        if account.apr_percent is None
        else str(account.apr_percent),
        "last_reconciled": None
        if account.last_reconciled is None
        else account.last_reconciled.isoformat(),
    }


def account_from_dict(data: Mapping[str, Any]) -> Account:
    return Account(
        data["id"],
        data["name"],
        AccountType[data["type"]],
        linked=data["linked"],
        paired_category_id=data["paired_category_id"],
        apr_percent=None
        if data["apr_percent"] is None
        else Decimal(data["apr_percent"]),
        last_reconciled=_optional_date(data["last_reconciled"]),
    )


def group_to_dict(group: CategoryGroup) -> dict[str, Any]:
    return {
        "id": group.id,
        "name": group.name,
        "category_ids": list(group.category_ids),
    }


def group_from_dict(data: Mapping[str, Any]) -> CategoryGroup:
    return CategoryGroup(data["id"], data["name"], list(data["category_ids"]))


def target_to_dict(target: Target) -> dict[str, Any]:
    match target:
        case MonthlyTarget():
            return {"type": "monthly", "amount": str(target.amount)}
        case CustomTarget():
            return {
                "type": "custom",
                "amount": str(target.amount),
                "sub_mode": target.sub_mode.name,
                "due_month": None
                if target.due_month is None
                else month_key(target.due_month),
            }
        case YearlyTarget():
            return {
                "type": "yearly",
                "amount": str(target.amount),
                "due_month": month_key(target.due_month),
            }
        case WeeklyTarget():
            return {
                "type": "weekly",
                "amount": str(target.amount),
                "weekday": target.weekday,
            }
        case DebtPaymentTarget():
            return {
                "type": "debt_payment",
                "account_id": target.account_id,
                "apr_percent": str(target.apr_percent),
                "monthly_payment": str(target.monthly_payment),
            }


def target_from_dict(data: Mapping[str, Any]) -> Target:
    kind = data["type"]
    if kind == "monthly":
        return MonthlyTarget(Decimal(data["amount"]))
    if kind == "custom":
        due = data["due_month"]
        return CustomTarget(
            Decimal(data["amount"]),
            CustomSubMode[data["sub_mode"]],
            None if due is None else parse_month(due),
        )
    if kind == "yearly":
        return YearlyTarget(Decimal(data["amount"]), parse_month(data["due_month"]))
    if kind == "weekly":
        return WeeklyTarget(Decimal(data["amount"]), int(data["weekday"]))
    if kind == "debt_payment":
        return DebtPaymentTarget(
            data["account_id"],
            Decimal(data["apr_percent"]),
            Decimal(data["monthly_payment"]),
        )
    raise PersistenceError(f"unknown target type {kind!r}")


def category_to_dict(category: Category) -> dict[str, Any]:
    return {
        "id": category.id,
        "name": category.name,
        "group_id": category.group_id,
        "note": category.note,
        "payment_account_id": category.payment_account_id,
        "target": None if category.target is None else target_to_dict(category.target),
    }


def category_from_dict(data: Mapping[str, Any]) -> Category:
    return Category(
        data["id"],
        data["name"],
        data["group_id"],
        note=data["note"],
        target=None if data["target"] is None else target_from_dict(data["target"]),
        payment_account_id=data["payment_account_id"],
    )


def payee_to_dict(payee: Payee) -> dict[str, Any]:
    return {"id": payee.id, "name": payee.name, "structural": payee.structural}


def payee_from_dict(data: Mapping[str, Any]) -> Payee:
    return Payee(data["id"], data["name"], data["structural"])


def schedule_to_dict(schedule: ScheduledTransaction) -> dict[str, Any]:
    return {
        "id": schedule.id,
        "account_id": schedule.account_id,
        "next_date": schedule.next_date.isoformat(),
        "amount": str(schedule.amount),
        "frequency": schedule.frequency.name,
        "anchor_day": schedule.anchor_day,
        "payee_id": schedule.payee_id,
        "category_id": schedule.category_id,
        "memo": schedule.memo,
    }


def schedule_from_dict(data: Mapping[str, Any]) -> ScheduledTransaction:
    return ScheduledTransaction(
        data["id"],
        data["account_id"],
        datetime.date.fromisoformat(data["next_date"]),
        Decimal(data["amount"]),
        Frequency[data["frequency"]],
        int(data["anchor_day"]),
        payee_id=data["payee_id"],
        category_id=data["category_id"],
        memo=data["memo"],
    )


def transaction_to_dict(txn: Transaction) -> dict[str, Any]:
    return {
        "id": txn.id,
        "account_id": txn.account_id,
        "date": txn.date.isoformat(),
        "amount": str(txn.amount),
        "payee_id": txn.payee_id,
        "category_id": txn.category_id,
        "splits": [
            {"category_id": s.category_id, "amount": str(s.amount), "memo": s.memo}
            for s in txn.splits
        ],
        "transfer_id": txn.transfer_id,
        "status": txn.status.name,
        "approved": txn.approved,
        "memo": txn.memo,
    }


def transaction_from_dict(data: Mapping[str, Any]) -> Transaction:
    return Transaction(
        data["id"],
        data["account_id"],
        datetime.date.fromisoformat(data["date"]),
        Decimal(data["amount"]),
        payee_id=data["payee_id"],
        category_id=data["category_id"],
        splits=tuple(
            SplitLine(s["category_id"], Decimal(s["amount"]), s["memo"])
            for s in data["splits"]
        ),
        transfer_id=data["transfer_id"],
        status=ClearedStatus[data["status"]],
        approved=data["approved"],
        memo=data["memo"],
    )
