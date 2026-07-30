from datetime import date

from ledger import YMonth


def test_next_and_prev_wrap_the_year() -> None:
    assert YMonth(2026, 12).next() == YMonth(2027, 1)
    assert YMonth(2027, 1).prev() == YMonth(2026, 12)
    assert YMonth(2026, 7).next() == YMonth(2026, 8)


def test_months_until_inclusive_counts_both_endpoints() -> None:
    assert YMonth(2026, 7).months_until_inclusive(YMonth(2026, 10)) == 4
    assert YMonth(2026, 7).months_until_inclusive(YMonth(2027, 1)) == 7
    assert YMonth(2026, 7).months_until_inclusive(YMonth(2026, 7)) == 1


def test_ordering_and_of() -> None:
    assert YMonth(2026, 7) < YMonth(2026, 8) < YMonth(2027, 1)
    assert YMonth.of(date(2026, 7, 29)) == YMonth(2026, 7)
    assert YMonth(2026, 7).last_day() == 31
    assert YMonth(2026, 2).last_day() == 28
