# Ledger Engine — Design Specification

A pure-Python budgeting engine implementing envelope-style (YNAB-like) budget
mechanics. Every rule in this document was derived from black-box behavioral
observation of a reference app; numeric examples are verified data points and
should become unit-test fixtures.

**Scope: engine only.** This library will never grow a UI. It exposes a
deterministic, in-memory domain model with explicit operations. Out of scope
permanently: rendering, sync, multi-device conflict handling, authentication,
bank import, notifications. Out of scope for now: multi-currency,
reports/analytics beyond Net Worth and Age of Money.

---

## 1. Object model

A **Plan** is the top-level container. Beneath it sit five entity types that
reference each other by stable identifier:

| Entity | Notes |
|---|---|
| Account | Typed (see §2); holds a balance and transactions |
| Category Group | Ordered container of Categories |
| Category | Ordered within its group; order is load-bearing (see §7.1) |
| Payee | Referenced by transactions via stable id, not copied string |
| Target | Attached 1:1 to a Category (optional) |

**Month scoping.** A "month view" is a query filtered to one month, not a
stored object. Category definitions (name, group, category-level note) are
month-independent; only Assigned, Activity, Available, and a per-month note
are scoped to a specific month. Category note and monthly note are two
separate storage locations.

## 2. Account taxonomy

| Class | Types | On budget? | Category linkage |
|---|---|---|---|
| CASH | Checking, Savings | Yes | Normal spending categories |
| CREDIT | Credit Card, Line of Credit | Yes | Auto-generated payment category (§5.3) |
| LOANS | Mortgage, Auto Loan | Yes | Paired at creation to an ordinary category (§5.4) |
| TRACKING | Investment, Asset, etc. | No | None — cannot be paired, ever |

- Payment-category linkage is triggered by the account being a
  *revolving-credit* type, not by having loan terms (Line of Credit behaves
  identically to Credit Card despite a simpler creation form).
- Loan subtype changes creation inputs (Mortgage has an escrow step, Auto
  Loan doesn't) but both produce the same structure: paired category, payoff
  simulation, optional Debt Payment Target.
- **Tracking accounts are a hard partition, not a display filter.** Funding
  one has zero effect on Ready to Assign; tracking accounts never enter any
  category's Activity. Their sole budget-adjacent role is Net Worth:
  `NetWorth = Σ balances of CASH + CREDIT + LOANS + TRACKING accounts`.
- Orthogonal flag: *linked* (bank-connected) vs. *unlinked* (manual). No
  effect on budget math.

## 3. Ready to Assign (RTA)

RTA is **cumulative across the plan's entire lifetime**, not a per-month
balance:

```
RTA = Σ (inflows categorized "Inflow: Ready to Assign", all accounts, all dates)
    − Σ (assigned to categories, all months, past AND future)
```

Consequences:

- Assigning in a future month is possible and draws down the same pool.
- RTA can go negative (over-assignment); the engine must represent this, not
  prevent it.
- The month summary ("Left Over from Last Month" / "Assigned in <month>" /
  "Activity" / "Available") is a decomposition of this rolling ledger, not an
  independent monthly budget.

## 4. Category monthly math

### 4.1 Rollover formula

```
Available(m) = carry_in(m) + Assigned(m) + Activity(m)

carry_in(m) = max(0, Available(m − 1))
```

Activity is negative for spending, positive for refunds/inflows categorized
directly to the category. Unspent Available rolls forward indefinitely;
unassigned income rolls forward in RTA indefinitely.

### 4.2 Overspending rollover (verified by controlled experiment)

A category **never carries a negative Available into the next month** — it
resets to a clean $0.00 there. Instead, uncovered overspending is deducted
from the *next month's RTA*, dollar for dollar.

Verified: July category overspent by $50, left uncovered → August Available
for that category = $0.00; August RTA dropped by exactly $50.00 (−$235.99 →
−$285.99). Covering the overspend *within July* by a real money-move (donor
category assigned −$50, overspent category assigned +$50 — net-zero RTA)
removed the August penalty entirely.

> Note: an earlier observation note claimed negative Available carries
> forward in-category. The controlled experiment above disproved that for
> cash overspending; implement reset-to-zero + RTA dock. Overspending *on a
> credit account* (which creates unbudgeted debt) was not stress-tested —
> flagged in §11.

## 5. Money movement operations

### 5.1 Assign / move

- **Assign(category, month, amount)**: sets a category's assigned value; the
  delta moves to/from RTA. Negative assignment is legal (returns money to
  RTA).
- **Move(from_category, to_category, month, amount)**: reallocates assigned
  money; net-zero RTA effect.

### 5.2 Transfers

A transfer between two on-budget accounts is a **linked pair of
transactions** sharing a system-generated payee `Transfer: [Account Name]`,
decrementing one account and incrementing the other identically. No category
is involved — no money entered or left the budget as a whole.

- Transfer payees are structural foreign-key markers, not user data: they
  cannot be merged or renamed.
- A transfer touching a tracking account moves money in or out of the
  budgeted world and therefore behaves as categorized outflow/inflow on the
  on-budget side.

### 5.3 Credit accounts and the payment-category mechanism

Creating an on-budget revolving-credit account auto-generates a category row
for it inside the "Credit Card Payments" group. A normal spending transaction
on a credit account is the one place a transaction silently touches a second
category:

- Spending $30 on the card against "Dining Out" decrements Dining Out's
  Available by $30 **and** increments the card's payment category by $30,
  with zero net RTA effect. The liability grows, but the budgeted dollars
  that "paid for" the purchase are simultaneously earmarked for the future
  card payment.
- **Record Payment** is the ordinary transfer mechanism (cash account →
  credit account) forced to use the linked payment category, which drains
  that category's Available and reduces the card's negative balance.

### 5.4 Loan accounts

- Pairing to an ordinary budget category is **required at creation** (choose
  existing or create new). The user assigns money monthly to that paired
  category.
- **Record Payment** is again a transfer, but internally decomposed into
  principal + interest via the account's stored rate and term
  (amortization, §6.4). Only the principal portion reduces the loan balance —
  the balance does *not* drop 1:1 with the payment amount.
- Manual activity entry supports: Add Payment, Add Credit, Add Fee or
  Charge, Add Interest.

## 6. Targets

A Target attaches 1:1 to a category. `amount_needed(category, month, today)`
is the single question every target type answers — it drives the
"Underfunded" figure and Auto-Assign (§7).

### 6.1 Type catalog and formulas

| Type | Formula for amount needed |
|---|---|
| Monthly | Flat: assign up to $X this month |
| Custom / Set aside $X | Add $X every month regardless of current balance (bills) |
| Custom / Fill up to $X | Top up: `max(0, X − balance_before_assignment)` (variable spending, capped accumulation) |
| Custom / Have a balance of $X, no due date | **$0 — no monthly pressure at all**, regardless of shortfall |
| Custom / Have a balance of $X by due month | `(X − current_balance) / months_remaining` (§6.2) |
| Yearly $X by due date | Identical engine to Custom-with-due-date (§6.2) |
| Weekly $X on weekday W | `X × (remaining occurrences of W from today through month-end)` (§6.3) |
| Debt Payment | Driven by paired loan account amortization (§6.4) |

### 6.2 Due-date amortization (Custom "have a balance" + Yearly)

```
months_remaining = calendar months from current month through due month,
                   INCLUSIVE of both endpoints
needed_per_month = (target_amount − current_balance) / months_remaining
```

Verified: $150 due October, current month July → 4 months → $37.50/mo.
$1,200 due January 2027, current month July 2026 → 7 months → $171.43/mo.

### 6.3 Weekly

Not month-based at all: counts actual remaining occurrences of the chosen
weekday between *today* and month-end.

Verified: $20/week due Saturdays, today Tue Jul 29 (last Saturday was Jul
25) → 0 occurrences left → $0 needed, target reads as met even with nothing
ever assigned. Same target switched to Fridays (Jul 31 remains) → $20.
Caveat: a "met" Weekly target can therefore mask a completely unfunded goal.

### 6.4 Debt Payment Target amortization

Three values are read from the paired account and never editable in the
target: current **Principal** (live balance), **interest rate**, and
**Minimum Payment** (the contractual payment configured at account
creation). The two user inputs — **Monthly Payment** and **Payoff Date** —
are bidirectional views onto one equation; editing either back-solves the
other.

```
r = annual_rate / 12          # monthly rate
n = −ln(1 − r·P / M) / ln(1 + r)   # months to payoff at payment M
total_interest = n·M − P
```

Special cases: `M ≤ r·P` never amortizes (no solution); `r = 0` degenerates
to `n = P / M`. Payment below the contractual Minimum Payment is legal but
should be flaggable (reference app warns about potential lender fees).

Verified vectors (also see §10):

| Principal | APR | Payment | Months to payoff | Total interest |
|---|---|---|---|---|
| $19,700.00 | 6% | $400.00 | ≈56.7 (4y 8m) | $2,972.05 |
| $19,700.00 | 6% | $600.00 | ≈36.0 (2y 11m) | $1,872.88 |
| $19,700.00 | 6% | $300.00 | 79 (6y 7m) | $4,239.24 |
| $19,700.00 | 6% | back-solve for 42 months (Jul 2026 → Jan 2030) | — | $521.19/mo, $2,189.86 |
| $199,633.33 | 5% | $1,200.00 | 284 (23y 8m) | $141,337.16 |
| $199,633.33 | 5% | $2,000.00 | 129 (10y 9m) | $58,994.14 (saves $82,343.02) |

### 6.5 Snoozing

Snoozing suppresses a target's funding requirement **for the current month
only**: it leaves Underfunded status/amount at zero and is excluded from
Auto-Assign Underfunded, surfacing under a distinct "Snoozed" state instead.
The target definition is untouched — the snooze is a per-month flag stored
separately from the persistent target object.

## 7. Auto-Assign

Two fundamentally different algorithm families. Whole-budget invocations go
through a preview (dry-run) then apply as **one atomic multi-category batch**;
per-category invocations apply immediately.

### 7.1 "Underfunded": greedy top-to-bottom walk

```
pool = RTA(month)                    # if RTA ≤ 0, nothing funds
for category in display order:       # group position, then position in group
    if snoozed or no target: continue
    need = target.amount_needed(...)
    if need ≤ 0: continue
    grant = min(need, pool)
    assign += grant; pool −= grant
    if pool == 0: break              # everything below gets strictly $0
```

Properties, all experimentally verified:

- **Only adds** money; never reduces a category; never drives RTA negative.
- **Position is the only priority.** Not amount-based, not an optimizer.
  Verified with scrambled amounts: needs of $400/$150/$200/$250/$50 in
  display order against a $964.01 pool → first three fully funded, fourth
  received the $214.01 remainder, fifth (needing only $50) received nothing.
  A knapsack optimizer would have skipped the $400 to fully fund the four
  smaller ones — that is explicitly not the behavior.
- Exactly one partial-fill boundary; everything below it gets zero.
- Reordering categories (drag between groups is allowed) directly changes
  funding priority.
- The per-category variant computes the same `need` for one category and
  applies immediately, e.g. covering a $50 overspend by pulling $50 from RTA.

### 7.2 Historical presets: direct set, uncapped

`Assigned Last Month`, `Spent Last Month`, `Average Assigned`,
`Average Spent` are a different mechanic entirely — a **set**, not an add:

- Each category's assigned value for the target month is **overwritten** to
  the historical figure — raised or lowered freely.
- `Spent Last Month` sources prior-month outflows (activity negated);
  `Average *` average over available history (with one month of history the
  average collapses to that month's value — verified identical previews).
- **No concept of "not enough money"**: not capped by RTA, and can push RTA
  further negative.
- With no prior-month history all four propose $0.00 for everything.

## 8. Transactions

### 8.1 Splits

A split transaction is **one register row** affecting the account balance
exactly once, fanning out into multiple category-allocation sub-records.
Invariant, enforced at **write time** (reject the write, don't display-fix):

```
Σ split allocations == transaction amount
```

### 8.2 Recurring / scheduled

"Recurring" is two independent mechanisms glued together:

1. Creating the schedule **immediately materializes the current due date's
   occurrence** as a real, pending transaction requiring approval.
2. Subsequent occurrences live in a separate scheduled region one period
   ahead, each becoming a real transaction only when its date arrives.

### 8.3 Reconciliation

Reconciling compares a user-entered real-world balance against the account's
current *cleared* balance. On confirmation, every previously-cleared
transaction is **retroactively locked** and the account stamped with a
reconciliation timestamp — a trust boundary freezing history, not a cosmetic
checkmark. On mismatch, an automatic already-cleared adjustment transaction
is inserted to force agreement. Cash-account adjustments are categorized as
"Inflow: Ready to Assign" — a discovered surplus raises RTA and a shortfall
docks it, exactly like an opening balance entering the budget. Credit and
tracking adjustments stay uncategorized and never touch RTA.

### 8.4 Payees

Transactions reference payees by stable id. **Merge(losing, surviving)**
repoints every historical transaction from the losing payee onto the
surviving one, then deletes the losing payee record — a foreign-key rewrite
plus delete, not a blended entity or cosmetic rename. System transfer payees
(§5.2) are excluded from merge/rename.

## 9. Queries and derived data

- **Built-in filters** (All / Underfunded / Overfunded / Money Available /
  Snoozed) are live predicates evaluated fresh against current month state —
  no persisted membership.
- **Custom Views** are the opposite: an explicitly enumerated, persisted,
  static list of category ids — a saved segment whose membership does not
  react to balance changes. Engine: store as a plain id list.
- **Age of Money**: rolling average of how long currently-spent dollars sat
  in accounts before being spent. Needs historical depth on both inflow and
  outflow sides to be meaningful. Exact algorithm unverified (§11).

## 10. Verified test vectors

Recorded from the reference app; each should become a test.

1. **RTA partition**: $5,000 into a tracking account → RTA unchanged.
   Inflow to an on-budget account categorized as RTA-inflow → RTA += amount.
2. **CC spend**: $30 on credit card vs. "Dining Out" → Dining Out Available
   −$30, card payment category +$30, RTA net change $0.
3. **Underfunded greedy walk**: pool $964.01; needs in display order
   $400 / $150 / $200 / $250 / $50 → grants $400 / $150 / $200 / $214.01 / $0.
4. **Overspend rollover**: −$50 uncovered in July → August carry-in $0 for
   the category, August RTA −$50 vs. July. Covering within July via
   move-money → no August penalty.
5. **Due-date targets**: $150 by Oct from Jul → $37.50/mo (4 months,
   inclusive). $1,200 by Jan 2027 from Jul 2026 → $171.43/mo (7 months).
6. **Weekly target**: $20 Saturdays, today 2026-07-29 → $0 needed.
   Same target on Fridays → $20 (one occurrence, Jul 31).
7. **Amortization** (formula `n = −ln(1 − r·P/M) / ln(1+r)`): the table in
   §6.4, including the $521.19 back-solve from a fixed payoff date.
8. **Historical preset is a set**: June assigned {Home Renovation $500,
   Groceries $400, Transportation $300}, June Groceries activity −$150.
   "Assigned Last Month" onto July overwrites *all* categories — including
   pulling categories with no June assignment down to $0 — and may push RTA
   negative. "Average Assigned" with a single history month proposes
   identical values. "Spent Last Month" proposes Groceries $150.
9. **Split invariant**: allocations summing ≠ $52.50 on a $52.50
   transaction → write rejected.

## 11. Open questions / unverified behavior

Not covered by observation; decide or verify before implementing:

- **Credit overspending**: rollover rule for overspending charged on a
  credit account (unbudgeted debt) — the §4.2 experiment covered cash only.
- **Loan payoff simulator** and **account exclusion/deletion** flows: never
  exercised.
- **Targets on credit-card payment categories**: interaction untested.
- **Weekly targets across a month boundary**: untested.
- **Age of Money**: exact windowing/algorithm unknown.
- Search/filtering beyond the built-in tabs, reports, multi-currency: out of
  scope (§ Scope).

## Appendix: relationship to `sample.py`

`sample.py` is a throwaway single-file prototype of most rules above,
written before this spec was consolidated. Treat this document, not the
prototype, as the source of truth — the real implementation lives in
`src/ledger/` with tests derived from §10.
