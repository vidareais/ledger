"""Engine error types."""


class LedgerError(Exception):
    """Base error for all ledger domain violations."""


class SplitMismatchError(LedgerError):
    """Split allocations do not sum to the transaction amount."""


class UnknownEntityError(LedgerError):
    """An operation referenced an id that does not exist in the plan."""
