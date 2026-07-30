"""Model rules adopted from api.yaml: payment-category guards, closed
accounts, transfer-payee identity, hidden categories, flags, account types."""

from datetime import date

import pytest

from ledger import (
    PAYMENT_GROUP_ID,
    RTA_INFLOW,
    AccountClass,
    AccountType,
    AutoAssignPreset,
    FlagColor,
    Frequency,
    LedgerError,
    MonthlyTarget,
    Plan,
    SplitLine,
    YMonth,
    money,
)

JUN = YMonth(2026, 6)
JUL = YMonth(2026, 7)
TODAY = date(2026, 7, 15)


def _plan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_account("card", "Card", AccountType.CREDIT_CARD)
    plan.add_category_group("g", "Group")
    plan.add_category("dining", "Dining Out", "g")
    plan.add_transaction(
        "checking", date(2026, 7, 1), 500, category_id=RTA_INFLOW, payee="Employer"
    )
    return plan


def test_payment_categories_cannot_categorize_anything() -> None:
    plan = _plan()
    with pytest.raises(LedgerError):
        plan.add_transaction(
            "checking", date(2026, 7, 5), -20, category_id="payment:card", payee="X"
        )
    with pytest.raises(LedgerError):
        plan.add_transaction(
            "checking",
            date(2026, 7, 5),
            -20,
            splits=[SplitLine("payment:card", money(-20))],
            payee="X",
        )
    with pytest.raises(LedgerError):
        plan.add_schedule(
            "checking",
            date(2026, 8, 1),
            -20,
            Frequency.MONTHLY,
            category_id="payment:card",
        )
    plan.assign("payment:card", JUL, 25)  # budgeting to it stays legal
    assert plan.assigned_for("payment:card", JUL) == money(25)


def test_closed_accounts_refuse_new_activity_but_keep_history() -> None:
    plan = _plan()
    plan.add_account("savings", "Savings", AccountType.SAVINGS, note="emergency fund")
    plan.add_transfer("checking", "savings", date(2026, 7, 3), 100)
    schedule, _ = plan.add_schedule(
        "savings", date(2026, 8, 1), -10, Frequency.MONTHLY, category_id="dining"
    )
    plan.close_account("savings")
    assert plan.accounts["savings"].closed
    assert schedule.id not in plan.schedules  # schedules removed at close
    with pytest.raises(LedgerError):
        plan.add_transaction(
            "savings", date(2026, 7, 5), -10, category_id="dining", payee="X"
        )
    with pytest.raises(LedgerError):
        plan.add_transfer("checking", "savings", date(2026, 7, 6), 10)
    with pytest.raises(LedgerError):
        plan.add_schedule(
            "savings", date(2026, 8, 1), -10, Frequency.MONTHLY, category_id="dining"
        )
    assert plan.account_balance("savings") == money(100)  # history queryable
    assert plan.accounts["savings"].note == "emergency fund"
    plan.reopen_account("savings")
    plan.add_transaction(
        "savings", date(2026, 7, 7), -10, category_id="dining", payee="X"
    )


def test_transfer_payees_are_keyed_by_account_not_name() -> None:
    plan = Plan()
    plan.add_account("s1", "Savings", AccountType.SAVINGS)
    plan.add_account("s2", "Savings", AccountType.SAVINGS)  # same display name
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_transaction(
        "checking", date(2026, 7, 1), 500, category_id=RTA_INFLOW, payee="Employer"
    )
    to_first, _ = plan.add_transfer("checking", "s1", date(2026, 7, 2), 10)
    to_second, _ = plan.add_transfer("checking", "s2", date(2026, 7, 3), 10)
    assert to_first.payee_id is not None and to_second.payee_id is not None
    assert to_first.payee_id != to_second.payee_id
    assert plan.payees[to_first.payee_id].transfer_account_id == "s1"
    assert plan.payees[to_second.payee_id].transfer_account_id == "s2"


def test_hidden_categories_are_skipped_by_auto_assign() -> None:
    plan = _plan()
    plan.add_category("fun", "Fun", "g")
    plan.set_target("dining", MonthlyTarget(money(50)))
    plan.set_target("fun", MonthlyTarget(money(80)))
    plan.set_category_hidden("dining", True)
    proposal = plan.preview_underfunded(JUL, TODAY)
    assert "dining" not in proposal
    assert proposal["fun"] == money(80)
    plan.assign("dining", JUN, 40)  # history so presets have a source month
    preset = plan.preview_preset(AutoAssignPreset.ASSIGNED_LAST_MONTH, JUL)
    assert "dining" not in preset
    plan.set_category_hidden("dining", False)
    plan.set_group_hidden("g", True)
    assert plan.preview_underfunded(JUL, TODAY) == {}


def test_flag_colors_flow_through_edits_and_schedules() -> None:
    plan = _plan()
    txn = plan.add_transaction(
        "checking",
        date(2026, 7, 5),
        -20,
        category_id="dining",
        payee="Cafe",
        flag_color=FlagColor.RED,
    )
    assert txn.flag_color is FlagColor.RED
    assert (
        plan.update_transaction(txn.id, flag_color=FlagColor.BLUE).flag_color
        is FlagColor.BLUE
    )
    assert plan.update_transaction(txn.id, flag_color=None).flag_color is None
    _, created = plan.add_schedule(
        "checking",
        date(2026, 7, 20),
        -10,
        Frequency.MONTHLY,
        category_id="dining",
        flag_color=FlagColor.GREEN,
        today=date(2026, 7, 20),
    )
    assert created[0].flag_color is FlagColor.GREEN  # materialized rows inherit


def test_internal_group_refuses_ordinary_categories() -> None:
    plan = _plan()
    assert plan.groups[PAYMENT_GROUP_ID].internal
    with pytest.raises(LedgerError):
        plan.add_category("x", "X", PAYMENT_GROUP_ID)


def test_new_account_types_classify_correctly() -> None:
    plan = Plan()
    wallet = plan.add_account("wallet", "Wallet", AccountType.CASH)
    assert wallet.account_class is AccountClass.CASH
    assert wallet.on_budget
    plan.add_category_group("bills", "Bills")
    plan.add_category("loan_cat", "Loan Payments", "bills")
    student = plan.add_account(
        "student",
        "Student Loan",
        AccountType.STUDENT_LOAN,
        paired_category_id="loan_cat",
        apr_percent="4.5",
    )
    assert student.account_class is AccountClass.LOANS
    assert not student.on_budget
    medical = plan.add_account(
        "medical",
        "Medical Debt",
        AccountType.MEDICAL_DEBT,
        paired_category_id="loan_cat",
        apr_percent="0",
    )
    assert medical.account_class is AccountClass.LOANS


def test_version_1_documents_still_load() -> None:
    plan = _plan()
    plan.assign("dining", JUL, 50)
    document = plan.to_dict()
    document["version"] = 1
    for account in document["accounts"]:
        del account["note"], account["closed"]
    for payee in document["payees"]:
        del payee["transfer_account_id"]
    for group in document["groups"]:
        del group["hidden"], group["internal"]
    for category in document["categories"]:
        del category["hidden"]
    for txn in document["transactions"]:
        del txn["flag_color"]
    reloaded = Plan.from_dict(document)
    assert reloaded.rta(JUL) == plan.rta(JUL)
    assert reloaded.groups[PAYMENT_GROUP_ID].internal  # normalized on load
