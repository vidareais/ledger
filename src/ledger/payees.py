"""Payees, referenced by transactions via stable id (DESIGN.md section 8.4)."""

from dataclasses import dataclass


@dataclass
class Payee:
    id: str
    name: str
    structural: bool = False
    transfer_account_id: str | None = None
