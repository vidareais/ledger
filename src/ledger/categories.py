"""Categories and category groups (DESIGN.md section 1)."""

from dataclasses import dataclass, field

from ledger.targets import Target


@dataclass
class Category:
    id: str
    name: str
    group_id: str
    note: str = ""
    target: Target | None = None
    payment_account_id: str | None = None
    hidden: bool = False

    @property
    def is_payment_category(self) -> bool:
        return self.payment_account_id is not None


@dataclass
class CategoryGroup:
    id: str
    name: str
    category_ids: list[str] = field(default_factory=list[str])
    hidden: bool = False
    internal: bool = False
