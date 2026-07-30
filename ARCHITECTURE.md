# Architecture

Diagrams of the ledger engine. All are Mermaid, rendered natively by GitHub
and by IDEs with Mermaid support. [DESIGN.md](DESIGN.md) holds the behavioral
specification these structures implement; [api.yaml](api.yaml) is the API
surface the engine is meant to back.

## Module dependencies

Foundations at the bottom, the `Plan` aggregate in the middle, persistence on
top. Arrows point at what a module imports.

```mermaid
flowchart TD
    storage["storage.py<br/>PlanStore protocol, JsonPlanStore"]
    plan["plan.py<br/>Plan aggregate: writes, guards,<br/>derived math, Auto-Assign, codecs entry"]
    serialization["serialization.py<br/>entity codecs, document format + version"]

    subgraph entities["entity modules"]
        accounts["accounts.py<br/>Account, AccountType, AccountClass"]
        categories["categories.py<br/>Category, CategoryGroup"]
        payees["payees.py<br/>Payee"]
        transactions["transactions.py<br/>Transaction, SplitLine,<br/>ClearedStatus, FlagColor"]
        schedules["schedules.py<br/>ScheduledTransaction, Frequency"]
        targets["targets.py<br/>five target families,<br/>amortization math"]
    end

    subgraph foundations["foundations"]
        money["money.py<br/>Decimal money"]
        month["month.py<br/>YMonth"]
        errors["errors.py"]
    end

    storage --> plan
    storage --> errors
    plan --> serialization
    plan --> entities
    plan --> foundations
    serialization --> entities
    serialization --> errors
    categories --> targets
    schedules --> transactions
    schedules --> month
    targets --> money
    targets --> month
```

## Domain model

The `Plan` owns every entity; identity is by string id. Frozen types are the
event-sourced facts, plain dataclasses are mutable aggregates.

```mermaid
classDiagram
    class Plan {
        accounts
        groups + group_order
        categories
        payees
        schedules
        transactions in ledger order
        assigned per category and month
        snoozed per category and month
    }
    class Account {
        id, name, type
        note, closed, linked
        apr_percent for loans
        last_reconciled
    }
    class CategoryGroup {
        id, name
        category_ids ordered
        hidden, internal
    }
    class Category {
        id, name, note
        hidden
        payment_account_id
    }
    class Target {
        <<union>>
        Monthly
        Custom set-aside, fill-up-to, have-balance
        Yearly
        Weekly
        DebtPayment
    }
    class Payee {
        id, name
        structural
        transfer_account_id
    }
    class Transaction {
        <<frozen>>
        id, date, amount
        status, approved, flag_color
        transfer_id
    }
    class SplitLine {
        <<frozen>>
        category_id, amount
    }
    class ScheduledTransaction {
        id, next_date, frequency
        anchor_day, flag_color
    }

    Plan "1" o-- "*" Account
    Plan "1" o-- "*" CategoryGroup : ordered
    Plan "1" o-- "*" Payee
    Plan "1" o-- "*" Transaction : ledger
    Plan "1" o-- "*" ScheduledTransaction
    CategoryGroup "1" o-- "*" Category : ordered
    Category "1" --> "0..1" Target
    Account "1" --> "0..1" Category : loan pairing
    Account "1" --> "0..1" Category : credit payment category
    Payee "0..1" --> "1" Account : transfer payee of
    Transaction "*" --> "1" Account
    Transaction "*" --> "0..1" Payee
    Transaction "*" --> "0..1" Category
    Transaction "1" *-- "*" SplitLine : fans out
    Transaction "1" -- "1" Transaction : transfer pair via transfer_id
    ScheduledTransaction "*" --> "1" Account
    ScheduledTransaction "*" --> "0..1" Category
    ScheduledTransaction "*" --> "0..1" Payee
```

## Stored facts vs. derived figures

The engine's central design decision: transactions and assigned amounts are
the only stored money facts. Everything a budget screen would show is
recomputed from them on demand, so month rollover is a query, not a
close-out step, and persistence is trivially lossless.

```mermaid
flowchart LR
    subgraph stored["stored facts — serialized"]
        TXN["transaction ledger<br/>dates, amounts, categories/splits,<br/>statuses, transfer links"]
        ASG["assigned amounts<br/>per category and month"]
    end
    subgraph derived["derived on demand — never stored"]
        ACT["activity(category, month)"]
        CIN["carry_in(category, month)"]
        AVL["available(category, month)"]
        RTA["rta(month)"]
        BAL["account_balance<br/>cleared_balance"]
        NW["net_worth()"]
        AOM["age_of_money()"]
        FUND["credit earmarking<br/>funded-spending walk"]
    end

    TXN --> ACT --> AVL
    TXN --> FUND --> ACT
    TXN --> BAL --> NW
    TXN -- "FIFO dollar buckets" --> AOM
    TXN -- "RTA inflows dated through month" --> RTA
    ASG --> AVL
    ASG -- "sum over all months" --> RTA
    CIN --> AVL
    AVL -- "max(0, prior month) recursion" --> CIN
    AVL -- "uncovered overspend docks later months" --> RTA
```

## Cleared-status lifecycle

Reconciliation is a trust boundary: it freezes history, and locked rows
refuse edits, deletion, and status changes.

```mermaid
stateDiagram-v2
    [*] --> UNCLEARED : add_transaction()
    [*] --> CLEARED : add_transaction(cleared=True)
    UNCLEARED --> CLEARED : set_cleared(True)
    CLEARED --> UNCLEARED : set_cleared(False)
    CLEARED --> RECONCILED : reconcile() locks all cleared rows
    note right of RECONCILED
        Locked. Refuses update, delete,
        and status changes. Payee merge
        still repoints (metadata only).
    end note
```

## Credit-card mechanics

Spending on a card moves budgeted dollars into the card's payment category;
paying the bill drains them. Ready to Assign never moves.

```mermaid
sequenceDiagram
    participant U as caller
    participant P as Plan
    participant DC as Dining Out (category)
    participant PC as Card Payment (category)
    participant CC as Card (account)
    participant CH as Checking (account)

    U->>P: add_transaction(card, -30, category=dining)
    P->>CC: balance -30 (debt grows)
    P->>DC: activity -30 (Available drops)
    Note over DC,PC: earmark = min(spend, running available)
    P->>PC: available +30 (reserved for the payment)
    Note over P: net RTA effect: zero

    U->>P: add_transfer(checking, card, 30)
    P->>CH: balance -30
    P->>CC: balance +30 (debt shrinks)
    P->>PC: available -30 (payment drains the reserve)
```
