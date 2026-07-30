"""DESIGN.md section 9: Age of Money, modeled on YNAB's metric."""

from datetime import date

from ledger import RTA_INFLOW, AccountType, Plan, YMonth


def _plan() -> Plan:
    plan = Plan()
    plan.add_account("checking", "Checking", AccountType.CHECKING)
    plan.add_category_group("g", "Group")
    plan.add_category("misc", "Misc", "g")
    return plan


def _income(plan: Plan, when: date, amount: int) -> None:
    plan.add_transaction(
        "checking", when, amount, category_id=RTA_INFLOW, payee="Employer"
    )


def _spend(plan: Plan, when: date, amount: int) -> None:
    plan.add_transaction("checking", when, -amount, category_id="misc", payee="Store")


def test_age_is_days_between_earning_and_spending() -> None:
    plan = _plan()
    assert plan.age_of_money() is None  # no history at all
    _income(plan, date(2026, 7, 1), 100)
    assert plan.age_of_money() is None  # inflows alone define nothing
    _spend(plan, date(2026, 7, 11), 30)  # age 10
    _spend(plan, date(2026, 7, 21), 50)  # age 20
    assert plan.age_of_money() == 15


def test_outflow_spanning_buckets_uses_a_weighted_average() -> None:
    plan = _plan()
    _income(plan, date(2026, 7, 1), 100)
    _income(plan, date(2026, 7, 11), 100)
    _spend(plan, date(2026, 7, 21), 150)  # 100 dollars aged 20, 50 aged 10
    assert plan.age_of_money() == 16  # (100*20 + 50*10) / 150 = 16.67


def test_credit_spending_ages_at_payment_time() -> None:
    plan = _plan()
    plan.add_account("card", "Card", AccountType.CREDIT_CARD)
    _income(plan, date(2026, 7, 1), 100)
    plan.assign("misc", YMonth(2026, 7), 50)
    plan.add_transaction(
        "card", date(2026, 7, 5), -30, category_id="misc", payee="Cafe"
    )
    assert plan.age_of_money() is None  # the purchase consumed no cash
    plan.add_transfer("checking", "card", date(2026, 7, 25), 30)
    assert plan.age_of_money() == 24  # the payment did


def test_cash_to_cash_transfers_are_neutral() -> None:
    plan = _plan()
    plan.add_account("savings", "Savings", AccountType.SAVINGS)
    _income(plan, date(2026, 7, 1), 100)
    plan.add_transfer("checking", "savings", date(2026, 7, 3), 50)
    assert plan.age_of_money() is None  # the pool is shared, nothing was spent
    plan.add_transaction(
        "savings", date(2026, 7, 6), -20, category_id="misc", payee="Store"
    )
    assert plan.age_of_money() == 5  # still funded by the Jul 1 income


def test_only_the_last_ten_outflows_are_averaged() -> None:
    plan = _plan()
    _income(plan, date(2026, 7, 1), 100)
    for day in range(2, 14):  # twelve $1 outflows, ages 1..12
        _spend(plan, date(2026, 7, day), 1)
    assert plan.age_of_money() == 7  # mean of ages 3..12 is 7.5, floored
    assert plan.age_of_money(sample=12) == 6  # mean of 1..12 is 6.5


def test_unfunded_spending_ages_zero_days() -> None:
    plan = _plan()
    _spend(plan, date(2026, 7, 5), 20)  # no inflow ever recorded
    assert plan.age_of_money() == 0
