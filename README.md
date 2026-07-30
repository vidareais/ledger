# ledger

A pure-Python **envelope-budgeting engine**, modeled on the mechanics of
YNAB-style budgeting apps. This is a library only — it has no UI and never
will. The behavioral specification lives in [DESIGN.md](DESIGN.md), which was
reverse-engineered from hands-on observation of a reference app; the test
suite pins the engine to that spec's verified numbers.

## What it does

- **Envelope budgeting**: income lands in *Ready to Assign* (a cumulative,
  plan-lifetime pool), gets assigned to categories, and rolls forward month
  to month. Uncovered overspending docks the next month's pool,
  dollar for dollar.
- **Accounts** in four classes — cash, credit, loans, tracking — with the
  budget partition rules that follow (credit cards get auto-generated
  payment categories; loans pair with a budget category; tracking accounts
  only ever touch Net Worth).
- **Credit-card mechanics**: spending on a card silently earmarks budgeted
  dollars into its payment category; paying the bill drains them.
- **Loan payments** decompose into principal and interest against the
  account's stored rate — the balance never drops 1:1 with the payment.
- **Targets**: monthly, custom (set-aside / fill-up-to / have-a-balance),
  yearly, weekly, and amortization-driven debt-payment targets, plus
  per-month snoozing.
- **Auto-Assign**: the greedy, display-order "Underfunded" walk and the four
  set-to-historical-value presets, both as preview → atomic apply.
- **Transactions**: splits (sum-validated at write time), double-entry
  transfers with structural payees, cleared/reconciled states with locked
  history, scheduled/recurring materialization with approval flags, edits
  and deletes that honor every invariant.
- **Payee integrity**: merge as a foreign-key rewrite, structural payees
  protected.
- **Reports**: Net Worth and a YNAB-style Age of Money (FIFO dollar aging
  over the cash pool).
- **Persistence**: a `PlanStore` abstraction with an atomic, versioned JSON
  document store.

## Design in one paragraph

Transactions and assigned amounts are the only stored facts. Balances,
category activity, carry-forward, Ready to Assign, and Age of Money are all
*derived* on demand, so month rollover is a query rather than a close-out
step, and persistence round-trips are trivially lossless. Money is `Decimal`
end to end, and the engine is clockless — every time-dependent operation
takes an explicit date, which keeps behavior deterministic and testable.
Diagrams of the module layout, domain model, and money flows live in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Quickstart

```python
from datetime import date

from ledger import (
    AccountType, JsonPlanStore, MonthlyTarget, Plan, RTA_INFLOW, YMonth, money,
)

plan = Plan()
jul = YMonth(2026, 7)

plan.add_account("checking", "Checking", AccountType.CHECKING)
plan.add_account("card", "Visa", AccountType.CREDIT_CARD)
plan.add_category_group("essentials", "Essentials")
plan.add_category("groceries", "Groceries", "essentials")
plan.set_target("groceries", MonthlyTarget(money(400)))

# Income lands in Ready to Assign; Auto-Assign gives every dollar a job.
plan.add_transaction(
    "checking", date(2026, 7, 1), 3000, category_id=RTA_INFLOW, payee="Employer"
)
plan.apply_assignments(jul, plan.preview_underfunded(jul, date(2026, 7, 1)))

# Spending on the card earmarks budgeted money for the future payment.
plan.add_transaction(
    "card", date(2026, 7, 5), -80, category_id="groceries", payee="Market"
)

print(plan.rta(jul))                        # 2600.00
print(plan.available("groceries", jul))     # 320.00
print(plan.available("payment:card", jul))  # 80.00

store = JsonPlanStore("plan.json")
store.save(plan)
plan = store.load()
```

## Development

Requires Python >= 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                # install dependencies
uv run pytest          # tests with coverage
uv run pytest --no-cov -q   # fast tests
uv run ruff check src tests # lint
uv run ruff format src tests# format
uv run pyright         # strict type checking
make check             # lint + typecheck + tests
```

## Layout

| Path | Contents |
|---|---|
| `src/ledger/plan.py` | The `Plan` aggregate: ledger writes, derived math, Auto-Assign, persistence entry points |
| `src/ledger/accounts.py`, `categories.py`, `payees.py`, `transactions.py`, `schedules.py` | Entity types |
| `src/ledger/targets.py` | Target formulas and loan amortization math |
| `src/ledger/money.py`, `month.py` | `Decimal` money helpers, calendar-month arithmetic |
| `src/ledger/serialization.py`, `storage.py` | Entity codecs, `PlanStore` + JSON store |
| `tests/` | The spec's verified vectors plus regression coverage, one file per subsystem |
| `DESIGN.md` | The behavioral specification — source of truth |
| `sample.py` | Throwaway early prototype kept for reference; not the implementation |

Behavior not yet specified (needs further observation of the reference app)
is tracked in DESIGN.md §11 — notably credit-account overspending rollover
and the loan payoff simulator.

## License

[MIT](LICENSE)
