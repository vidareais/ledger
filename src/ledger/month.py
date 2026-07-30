"""Calendar month arithmetic."""

import calendar
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, order=True)
class YMonth:
    """A calendar month, e.g. YMonth(2026, 7) is July 2026."""

    year: int
    month: int

    @classmethod
    def of(cls, day: date) -> YMonth:
        return cls(day.year, day.month)

    def next(self) -> YMonth:
        if self.month == 12:
            return YMonth(self.year + 1, 1)
        return YMonth(self.year, self.month + 1)

    def prev(self) -> YMonth:
        if self.month == 1:
            return YMonth(self.year - 1, 12)
        return YMonth(self.year, self.month - 1)

    def months_until_inclusive(self, other: YMonth) -> int:
        """Calendar months from self through other, counting both endpoints.

        DESIGN.md section 6.2: Jul 2026 through Jan 2027 is 7 months.
        """
        return (other.year - self.year) * 12 + (other.month - self.month) + 1

    def last_day(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"
