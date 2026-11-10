"""What an analysis finds and what a change gives: the dates of a document,
which of them to change, the result, and how it is told in a few words.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from cartiglio.i18n import _

from .dates import DateStyle


class CartiglioError(ValueError):
    """The file cannot be processed: not a PDF, damaged, password-protected,
    signed, or the PDF engine is missing."""


class NothingToChange(Exception):
    """No date in the title block (or none selected)."""


@dataclass
class DateHit:
    """One date found in the document."""
    index: int                 # 1-based, in reading order: the number shown to the user
    text: str                  # as written on the page
    value: date
    style: DateStyle
    rect: tuple                # displayed page coordinates (x0, y0, x1, y1)
    in_block: bool             # inside the title block
    page: int = 0              # 0-based page number
    # what is needed to rewrite it (unrotated page coordinates)
    raw_rect: tuple = ()
    origin: tuple = ()         # baseline start of the first glyph
    direction: tuple = (1.0, 0.0)
    length: float = 0.0        # advance from origin to the end of the last glyph
    font: str = ""
    size: float = 0.0
    color: int = 0
    rotation: int = 0          # text direction, in degrees (0, 90, 180, 270)


@dataclass
class Analysis:
    path: Path
    pages: int
    is_document: bool          # report/cover (whole first page) vs drawing (a corner)
    zone: tuple                # page 1 title block, displayed coordinates (x0, y0, x1, y1)
    zone_guessed: bool         # no title-block labels found: bottom-right corner assumed
    page_size: tuple           # page 1 displayed (width, height) in pt
    dates: list = field(default_factory=list)   # page 1, then later pages if scanned
    # Later pages with a title block of their own (the sheets of a set of
    # single-line diagrams, each with its small block in the corner).
    later_pages: int = 0       # pages after the first with a date in their title block
    repeated: bool = False     # ...on (nearly) every later page, in the same place
    all_pages: bool = False    # change those too: on by default when repeated

    @property
    def block_dates(self):
        """Title-block dates on page 1, plus those of later pages when
        all_pages is set."""
        return [d for d in self.dates if d.in_block and (d.page == 0 or self.all_pages)]

    @property
    def later_dates(self):
        return [d for d in self.dates if d.in_block and d.page > 0]

    def default_selection(self) -> set:
        """The dates to change unless the user says otherwise: in the title
        block, and equal to the most recent date of that page's block. Older
        ones are usually earlier revisions, whose history must not be
        rewritten."""
        latest = {}
        for d in self.block_dates:
            latest[d.page] = max(latest.get(d.page, d.value), d.value)
        return {d.index for d in self.block_dates if d.value == latest[d.page]}


@dataclass
class Result:
    dest: Path
    changed: int               # dates replaced


def _date_list(dates) -> str:
    values = sorted({d.text for d in dates}, key=lambda t: t)
    return ", ".join(values[:3]) + ("…" if len(values) > 3 else "")


def describe(analysis: Analysis, selection=None) -> str:
    """What was found, in a few words: '4 dates in the title block
    (14/08/2026) · 27 more pages with a title block (14/08/2026)'."""
    block = analysis.block_dates
    first = [d for d in block if d.page == 0]
    later = [d for d in block if d.page > 0]
    parts = []
    if first:
        n = len(first)
        parts.append((_("1 date in the title block ({dates})") if n == 1
                      else _("{n} dates in the title block ({dates})")).format(
                          n=n, dates=_date_list(first)))
    elif analysis.dates and not later:
        others = len([d for d in analysis.dates if d.page == 0])
        parts.append(_("no date in the title block ({n} elsewhere on the page)").format(
            n=others))
    elif not later:
        parts.append(_("no date on the first page"))
    if later:
        parts.append(_("{n} more pages with a title block ({dates})").format(
            n=len({d.page for d in later}), dates=_date_list(later)))
    elif analysis.later_pages and not analysis.all_pages:
        parts.append(_("title block also on {n} more pages (not included)").format(
            n=analysis.later_pages))
    text = " · ".join(parts)
    if block:
        selection = analysis.default_selection() if selection is None else selection
        if len(selection) != len(block):
            text += " · " + _("{k} to change").format(k=len(selection))
    return text
