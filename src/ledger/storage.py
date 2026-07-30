"""Plan persistence backends behind the PlanStore abstraction."""

import json
import os
from pathlib import Path
from typing import Any, Protocol, cast

from ledger.errors import PersistenceError
from ledger.plan import Plan


class PlanStore(Protocol):
    """Anything that can persist a whole Plan and bring it back."""

    def exists(self) -> bool: ...

    def load(self) -> Plan: ...

    def save(self, plan: Plan) -> None: ...


class JsonPlanStore:
    """Stores the plan as one versioned JSON document. Saves are atomic:
    written to a sibling temp file, then swapped into place, so a crash
    mid-save never corrupts the previous document."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> Plan:
        if not self.exists():
            raise PersistenceError(f"no plan document at {self.path}")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PersistenceError(f"invalid JSON in {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise PersistenceError("not a ledger plan document")
        return Plan.from_dict(cast(dict[str, Any], data))

    def save(self, plan: Plan) -> None:
        payload = json.dumps(plan.to_dict(), indent=2) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self.path.with_name(self.path.name + ".tmp")
        scratch.write_text(payload, encoding="utf-8")
        os.replace(scratch, self.path)
