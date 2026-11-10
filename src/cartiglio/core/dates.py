"""Dates as they are written on a drawing: found, read, written again in
the same style, and as the user types them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Day, separator, month, the same separator again, year. Not glued to other
# digits, letters or separators: "FK-5-1-12" and "50-51-51N" are not dates.
_DATE_RE = re.compile(r"(?<![\w/.\-])(\d{1,2})([/.\-])(\d{1,2})\2(\d{4}|\d{2})(?![/.\-]?\d)(?!\w)")


@dataclass(frozen=True)
class DateStyle:
    """How a date is written: kept when it is replaced."""
    day_digits: int     # 1 or 2 ("4/8/2026" vs "04/08/2026")
    month_digits: int
    sep: str            # "/", "." or "-"
    year_digits: int    # 2 or 4

    def format(self, value: date) -> str:
        day = f"{value.day:0{self.day_digits}d}"
        month = f"{value.month:0{self.month_digits}d}"
        year = f"{value.year % 100:02d}" if self.year_digits == 2 else f"{value.year:04d}"
        return f"{day}{self.sep}{month}{self.sep}{year}"


def parse_date_match(m: re.Match):
    """(date, DateStyle) for a _DATE_RE match, or None when it is not a real
    calendar date (31/02/2026, 14/13/2026)."""
    day, sep, month, year = m.group(1), m.group(2), m.group(3), m.group(4)
    y = int(year) + (2000 if len(year) == 2 else 0)
    try:
        value = date(y, int(month), int(day))
    except ValueError:
        return None
    return value, DateStyle(len(day), len(month), sep, len(year))


def parse_user_date(text: str) -> date:
    """The new date as typed by the user: dd/mm/yyyy (also with . or -, and
    two-digit years), or ISO yyyy-mm-dd. Raises ValueError."""
    text = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DATE_RE.fullmatch(text)
    parsed = parse_date_match(m) if m else None
    if parsed is None:
        raise ValueError(text)
    return parsed[0]


def format_user_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def date_label(value: date) -> str:
    """16-10-2026: safe in file names everywhere."""
    return value.strftime("%d-%m-%Y")
