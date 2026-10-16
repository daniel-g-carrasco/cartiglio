#!/usr/bin/env python3
"""Cartiglio — change the dates in the title block of PDF drawings and documents.

The first page of every PDF is searched for dates (14/08/2026, 14.08.26 and
the like). Those inside the title block — the table in a corner of a
drawing, the cover of a report — are replaced by a new date. The old glyphs
are really removed from the page content, not hidden under a white box, and
the new date is written in the same place, font, size, colour, direction and
width: it stays text that can be searched and copied.

Before anything is saved the page is rendered again and compared with the
original: if anything changed outside the replaced dates, the file is left
alone. The result goes to a new folder; the original is never modified.

Run with file/folder arguments for CLI mode, or without arguments for the
GTK 4 GUI.
"""

from __future__ import annotations

import argparse
import configparser
import gettext
import itertools
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

__version__ = "1.0.0"

APP_ID = "io.github.daniel_g_carrasco.cartiglio"
APP_NAME = "Cartiglio"
GITHUB_REPO = "daniel-g-carrasco/cartiglio"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
DOWNLOADS_URL = f"https://github.com/{GITHUB_REPO}/releases/download/"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
TEXTDOMAIN = "cartiglio"

# Windows shell integration identifier (see the "Windows" section below and
# installer/cartiglio.iss, which must stay in sync with it).
WIN_VERB = "Cartiglio.redate"

_ = gettext.NullTranslations().gettext  # rebound by setup_i18n()


def _mark(label: str) -> None:
    """Start-up profiling aid: append a timestamp to $CARTIGLIO_STARTUP_LOG."""
    path = os.environ.get("CARTIGLIO_STARTUP_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{time.time():.3f} {label}\n")
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Localisation (GNU gettext; catalogues in po/, compiled by tools/compile_po.py)
# ---------------------------------------------------------------------------

LANGUAGES = ("en", "it")


def _locale_dirs():
    base = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
    yield base / "share" / "locale"   # PyInstaller bundle
    yield base / "locale"             # source checkout, after tools/compile_po.py
    for prefix in ("/app", "/usr/local", "/usr"):
        yield Path(prefix) / "share" / "locale"


def detect_language() -> str:
    """Language of the user's desktop: the Windows display language, or the
    usual environment variables elsewhere. English when in doubt."""
    if sys.platform == "win32":
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            return "it" if langid == 0x10 else "en"
        except (AttributeError, OSError):
            return "en"
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return "it" if value.split(":")[0].lower().startswith("it") else "en"
    return "en"


def setup_i18n(choice: str = "auto") -> str:
    """Install the translation for `choice` ("auto", "it" or "en") and return
    the language in use. Must run before any user-visible string is built."""
    global _
    lang = choice if choice in LANGUAGES else detect_language()
    if sys.platform == "win32" or choice in LANGUAGES:
        # GTK's own strings (dialog buttons, file chooser) come from libintl,
        # which honours LANGUAGE: keep them in step with ours.
        os.environ["LANGUAGE"] = lang
    translation = gettext.NullTranslations()
    for directory in _locale_dirs():
        if gettext.find(TEXTDOMAIN, str(directory), languages=[lang]):
            translation = gettext.translation(TEXTDOMAIN, str(directory), languages=[lang])
            break
    _ = translation.gettext
    return lang


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Title block
#
# A drawing has its title block in one corner, where words like DATA,
# REDAZIONE, SCALA or REV crowd together; dates elsewhere on the sheet
# (notes, surveys, details) are left alone. A report has it on the cover, so
# there the whole first page counts.
# ---------------------------------------------------------------------------

TITLE_WORDS = frozenset("""
    DATA DATE REDAZIONE REDATTO REDATTA DISEGNATO DISEGNATORE CONTROLLO
    CONTROLLATO VERIFICATO APPROVAZIONE APPROVATO APPROVATA EMISSIONE EMISS
    REV REVISIONE REVISIONI REVISION SCALA SCALE ELABORATO TAVOLA FOGLIO
    COMMESSA COMMITTENTE CLIENTE PROGETTO PROGETTISTA TITOLO FILE FORMATO
    CODICE DRAWN CHECKED APPROVED DESIGNED SHEET TITLE PROJECT CLIENT RED
    CONTR AUT DESCRIZIONE DESCRIPTION
""".split())

_DOCUMENT_MAX_SIDE = 1191 * 1.02   # pt: up to A3, a page is a document cover
_ZONE_REACH = 60 * 72 / 25.4       # pt: how far the block reaches past its labels
_GUESS_W, _GUESS_H = 0.40, 0.35    # no labels found: bottom-right corner share


def _norm_word(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


def title_block_zone(page_rect, words, is_document: bool):
    """Where the title block is, in displayed page coordinates:
    (x0, y0, x1, y1, guessed). `words` are (x0, y0, x1, y1, text) already in
    displayed coordinates."""
    W, H = page_rect
    if is_document:
        return (0.0, 0.0, W, H, False)
    labels = [w for w in words if _norm_word(w[4]) in TITLE_WORDS]
    if not labels:
        return (W * (1 - _GUESS_W), H * (1 - _GUESS_H), W, H, True)
    cx, cy = W / 2, H / 2

    def corner(w):
        mx, my = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        return (mx >= cx, my >= cy)  # (right, bottom)

    counts = Counter(corner(w) for w in labels)
    # Most labels wins; ties go to the usual places, bottom right first.
    order = [(True, True), (False, True), (True, False), (False, False)]
    best = max(order, key=lambda c: (counts.get(c, 0), -order.index(c)))
    mine = [w for w in labels if corner(w) == best]
    x0 = min(w[0] for w in mine) - _ZONE_REACH
    y0 = min(w[1] for w in mine) - _ZONE_REACH
    x1 = max(w[2] for w in mine) + _ZONE_REACH
    y1 = max(w[3] for w in mine) + _ZONE_REACH
    right, bottom = best
    # The block sits against the sheet border on its corner's sides, and does
    # not cross into the other half of the sheet.
    if right:
        x0, x1 = max(x0, cx), W
    else:
        x0, x1 = 0.0, min(x1, cx)
    if bottom:
        y0, y1 = max(y0, cy), H
    else:
        y0, y1 = 0.0, min(y1, cy)
    return (x0, y0, x1, y1, False)


# ---------------------------------------------------------------------------
# PDF core (PyMuPDF)
# ---------------------------------------------------------------------------

_CHUNK = 1 << 20               # 1 MiB: I/O granularity for progress reporting
_VERIFY_PIXELS = 4_000_000     # per rendered page for the before/after check
_VERIFY_LEVEL = 64             # grey-level difference that counts as a change
_VERIFY_TOLERANCE = 40         # pixels allowed to differ (anti-aliasing noise)


class CartiglioError(ValueError):
    """The file cannot be processed: not a PDF, damaged, password-protected,
    signed, or the PDF engine is missing."""


class NothingToChange(Exception):
    """No date in the title block (or none selected)."""


def load_engine():
    """Import PyMuPDF. Kept out of the module level so that --help, --version
    and the GUI start-up do not pay for it."""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # older PyMuPDF releases
        except (ImportError, OSError) as e:
            raise CartiglioError(_("the PDF engine is not available ({error}); "
                                   "install it with: pip install pymupdf").format(error=e)) from None
    pymupdf.TOOLS.mupdf_display_errors(False)  # damaged xrefs are repaired silently
    pymupdf.TOOLS.mupdf_display_warnings(False)
    return pymupdf


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


def read_bytes(src: Path, progress=None, share: float = 1.0) -> bytes:
    """Read a whole file, reporting progress(fraction * share)."""
    size = max(src.stat().st_size, 1)
    raw = bytearray()
    with open(src, "rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            raw += chunk
            if progress:
                progress(share * min(len(raw), size) / size)
    return bytes(raw)


def _open(pymupdf, data: bytes):
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # PyMuPDF raises a variety of types for broken input
        raise CartiglioError(_("not a readable PDF document")) from None
    if doc.needs_pass:
        doc.close()
        raise CartiglioError(_("the PDF is password-protected"))
    if doc.page_count == 0:
        doc.close()
        raise CartiglioError(_("the PDF has no pages"))
    return doc


def _is_signed(doc) -> bool:
    try:
        return doc.get_sigflags() > 0
    except Exception:
        return False


def _right_angle(direction) -> int | None:
    """Text direction (dx, dy) in MuPDF's y-down space -> rotation for
    insert_text (counter-clockwise degrees), or None if not a right angle."""
    dx, dy = direction
    for rot, (ex, ey) in ((0, (1, 0)), (90, (0, -1)), (180, (-1, 0)), (270, (0, 1))):
        if abs(dx - ex) < 1e-3 and abs(dy - ey) < 1e-3:
            return rot
    return None


def _find_dates(page, textpage, rotation_matrix):
    """Every date on an (unrotated) page: list of DateHit without in_block."""
    hits = []
    raw = page.get_text("rawdict", textpage=textpage)
    for block in raw["blocks"]:
        for line in block.get("lines", ()):
            chars = [(c, span) for span in line["spans"] for c in span["chars"]]
            text = "".join(c["c"] for c, _span in chars)
            for m in _DATE_RE.finditer(text):
                parsed = parse_date_match(m)
                if parsed is None:
                    continue
                rot = _right_angle(line["dir"])
                if rot is None:
                    continue  # slanted text: not something a title block has
                glyphs = chars[m.start():m.end()]
                first, span = glyphs[0]
                x0 = min(c["bbox"][0] for c, _s in glyphs)
                y0 = min(c["bbox"][1] for c, _s in glyphs)
                x1 = max(c["bbox"][2] for c, _s in glyphs)
                y1 = max(c["bbox"][3] for c, _s in glyphs)
                dx, dy = line["dir"]
                ox, oy = first["origin"]
                last = glyphs[-1][0]["bbox"]
                length = max((px - ox) * dx + (py - oy) * dy
                             for px in (last[0], last[2]) for py in (last[1], last[3]))
                shown = _transform_rect((x0, y0, x1, y1), rotation_matrix)
                hits.append(DateHit(
                    index=0, text=m.group(0), value=parsed[0], style=parsed[1],
                    rect=shown, in_block=False, page=page.number, raw_rect=(x0, y0, x1, y1),
                    origin=(ox, oy), direction=(dx, dy), length=length,
                    font=span["font"], size=span["size"], color=span["color"],
                    rotation=rot))
    # reading order on the sheet as displayed: top to bottom, left to right
    hits.sort(key=lambda h: (round(h.rect[1], 0), h.rect[0]))
    return hits


def _transform_rect(rect, matrix):
    """Rect (x0, y0, x1, y1) through a pymupdf.Matrix, normalised."""
    a, b, c, d, e, f = matrix
    pts = [(x * a + y * c + e, x * b + y * d + f)
           for x in (rect[0], rect[2]) for y in (rect[1], rect[3])]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


_REPEATED_SHARE = 0.8          # of the later pages, for a title block on every page
_SAME_PLACE = 3.0              # pt: "the same place" on two pages


def _analyse_page(page, is_document: bool):
    """Dates of one page, flagged in_block, and its title-block zone
    (x0, y0, x1, y1, guessed) in displayed coordinates."""
    shown = page.rect
    rotation_matrix = tuple(page.rotation_matrix)
    rotation = page.rotation
    page.set_rotation(0)  # text and edits in the page's own coordinates
    try:
        # One text extraction for both uses: on a heavy drawing it is the
        # slow part (the whole content stream is interpreted).
        textpage = page.get_textpage()
        dates = _find_dates(page, textpage, rotation_matrix)
        words = [_transform_rect(w[:4], rotation_matrix) + (w[4],)
                 for w in page.get_text("words", textpage=textpage)]
    finally:
        page.set_rotation(rotation)
    zone = title_block_zone((shown.width, shown.height), words, is_document)
    zx0, zy0, zx1, zy1, _guessed = zone
    for h in dates:
        mx, my = (h.rect[0] + h.rect[2]) / 2, (h.rect[1] + h.rect[3]) / 2
        h.in_block = zx0 <= mx <= zx1 and zy0 <= my <= zy1
    return dates, zone


def _later_page_dates(page):
    """Dates of a page after the first, flagged in_block only where the
    block's labels were found (no guessing on the pages of a report)."""
    dates, zone = _analyse_page(page, False)
    if zone[4]:
        for d in dates:
            d.in_block = False
    return dates


def _spot(d):
    return (round(d.rect[0] / _SAME_PLACE), round(d.rect[1] / _SAME_PLACE))


def _analyse_doc(doc, path, all_pages=None, progress=None) -> Analysis:
    """all_pages: None to look at the later pages and decide (a title block
    repeated on every sheet), True to take their title blocks anyway, False
    to stay on the first page."""
    first = doc[0]
    shown = first.rect
    is_document = doc.page_count > 1 or max(shown.width, shown.height) <= _DOCUMENT_MAX_SIDE
    dates, zone = _analyse_page(first, is_document)
    analysis = Analysis(Path(path), doc.page_count, is_document, zone[:4], zone[4],
                        (shown.width, shown.height), dates)

    # Later pages: page 2 says whether there is anything to look for, so a
    # long report costs one page, not two hundred.
    later = []
    if doc.page_count > 1 and all_pages is not False:
        second = _later_page_dates(doc[1])
        if any(d.in_block for d in second) or all_pages:
            later = second
            for n in range(2, doc.page_count):
                later += _later_page_dates(doc[n])
                if progress:
                    progress(0.8 + 0.2 * n / doc.page_count)
    block = [d for d in later if d.in_block]
    if block:
        # Where the repeated block keeps its date: the most common position.
        # A date there is the block's date even on a sheet where the labels
        # pointed elsewhere (a legend table headed CODICE / DATA / DESCRIZIONE
        # can outnumber the labels of the small block in the corner).
        spot = Counter(_spot(d) for d in block).most_common(1)[0][0]
        placed = {d.page for d in later if _spot(d) == spot}
        analysis.repeated = len(placed) >= _REPEATED_SHARE * (doc.page_count - 1)
        if analysis.repeated:
            # The same block on every sheet: its date is the one in that place,
            # and only that one (the legend's dates are not the block's).
            for d in later:
                d.in_block = _spot(d) == spot
        block = [d for d in later if d.in_block]
        analysis.later_pages = len({d.page for d in block})
        analysis.all_pages = analysis.repeated if all_pages is None else bool(all_pages)
        analysis.dates += block
    # numbering for the user: page 1 in reading order, then the later pages
    for i, d in enumerate(analysis.dates, 1):
        d.index = i
    return analysis


def analyse_file(src: Path, progress=None, all_pages=None) -> Analysis:
    """Find the dates of src and where its title block is: on the first
    page, and on the later ones when they carry a title block too (see
    _analyse_doc for all_pages).
    Raises CartiglioError for unsupported input, OSError on I/O problems."""
    pymupdf = load_engine()
    src = Path(src)
    data = read_bytes(src, progress, 0.7)
    doc = _open(pymupdf, data)
    try:
        if _is_signed(doc):
            raise CartiglioError(_("the PDF is digitally signed: changing it "
                                   "would invalidate the signature"))
        result = _analyse_doc(doc, src, all_pages, progress)
    finally:
        doc.close()
    if progress:
        progress(1.0)
    return result


# --- fonts -----------------------------------------------------------------
#
# The fonts embedded in a PDF are subsets, often without a usable character
# map and without the digits the old date did not need, so they cannot write
# the new date. The same font is looked up among the installed ones (Windows
# font registry, fontconfig elsewhere); failing that, Helvetica is used. In
# both cases the width is matched to the old date, so a narrow or stretched
# font (AutoCAD's width factor) still fills the same space.

_METRIC_ALIASES = {  # fontconfig substitutes with the same metrics
    "arial": ("liberationsans", "arimo"),
    "arialnarrow": ("liberationsansnarrow",),
    "helvetica": ("liberationsans", "arimo", "nimbussans"),
    "timesnewroman": ("liberationserif", "tinos"),
    "couriernew": ("liberationmono", "cousine"),
}


def _font_key(name: str) -> str:
    """'HVUAQN+Arial-BoldMT' -> 'arialbold'; 'Arial Narrow' -> 'arialnarrow'."""
    name = name.split("+", 1)[-1]
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for suffix in ("psmt", "mt", "ps", "regular", "roman"):
        if key.endswith(suffix) and len(key) > len(suffix) + 2:
            key = key[: -len(suffix)]
            break
    return key


def _split_family(name: str):
    """'ArialNarrow-Bold' -> ('Arial Narrow', 'Bold') for fontconfig."""
    name = name.split("+", 1)[-1]
    family, _sep, style = name.partition("-")
    family = re.sub(r"(PS)?MT$", "", family)
    family = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family)
    style = re.sub(r"(PS)?MT$", "", style)
    return family.strip(), style.strip() or "Regular"


def _base14(name: str) -> str:
    """Closest PDF standard font, for when nothing better is installed."""
    key = name.lower()
    bold = "bold" in key or "black" in key or "heavy" in key
    italic = "italic" in key or "oblique" in key
    if "times" in key or "serif" in key and "sans" not in key:
        return {(0, 0): "tiro", (1, 0): "tibo", (0, 1): "tiit", (1, 1): "tibi"}[bold, italic]
    if "courier" in key or "mono" in key:
        return {(0, 0): "cour", (1, 0): "cobo", (0, 1): "coit", (1, 1): "cobi"}[bold, italic]
    return {(0, 0): "helv", (1, 0): "hebo", (0, 1): "heit", (1, 1): "hebi"}[bold, italic]


class FontFinder:
    """Installed font files by name, looked up once per name."""

    def __init__(self):
        self._cache: dict[str, str | None] = {}
        self._registry = None

    def find(self, name: str) -> str | None:
        key = _font_key(name)
        if key not in self._cache:
            self._cache[key] = (self._windows(key) if sys.platform == "win32"
                                else self._fontconfig(name, key))
        return self._cache[key]

    def _windows(self, key):
        if self._registry is None:
            self._registry = {}
            import winreg
            fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
                except OSError:
                    continue
                with k:
                    i = 0
                    while True:
                        try:
                            label, value, _kind = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        i += 1
                        if not str(value).lower().endswith((".ttf", ".otf")):
                            continue  # collections (.ttc) and bitmap fonts
                        label = re.sub(r"\s*\((TrueType|OpenType)\)\s*$", "", label)
                        path = Path(value) if os.path.isabs(value) else fonts_dir / value
                        self._registry.setdefault(_font_key(label), str(path))
        path = self._registry.get(key)
        return path if path and os.path.isfile(path) else None

    @staticmethod
    def _fontconfig(name, key):
        family, style = _split_family(name)
        try:
            out = subprocess.run(
                ["fc-match", "-f", "%{family[0]}\n%{file}", f"{family}:style={style}"],
                capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        got_family, _nl, path = out.partition("\n")
        got = _font_key(got_family)
        base = _font_key(family)
        if got == base or got in _METRIC_ALIASES.get(base, ()):
            if path.lower().endswith((".ttf", ".otf")) and os.path.isfile(path):
                return path
        return None


def _subset_font(path: str, text: str) -> bytes | None:
    """The font reduced to the glyphs of `text` (a few kB instead of hundreds),
    or None without fontTools: the whole font is then embedded."""
    try:
        import io
        import logging
        from fontTools import subset as fts
    except ImportError:
        return None
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    try:
        opt = fts.Options()
        opt.name_IDs = ["*"]
        font = fts.load_font(path, opt)
        subsetter = fts.Subsetter(opt)
        subsetter.populate(text=text)
        subsetter.subset(font)
        buf = io.BytesIO()
        font.save(buf)
        return buf.getvalue()
    except Exception:
        return None


_FONTS = FontFinder()
_DATE_GLYPHS = "0123456789/.-"     # all a date needs, old or new
_WRITING_FONTS: dict = {}          # original font name -> (alias, font data, pymupdf.Font)


def _writing_font(pymupdf, name: str):
    """(name to use on the page, font data to embed or None, pymupdf.Font
    for measuring) for writing dates that were in font `name`. The same
    subset serves every page and every file: set up once per name."""
    if name not in _WRITING_FONTS:
        path = _FONTS.find(name)
        if path:
            data = _subset_font(path, _DATE_GLYPHS) or Path(path).read_bytes()
            alias = "Ctg" + re.sub(r"[^A-Za-z0-9]", "", _font_key(name))[:20]
            _WRITING_FONTS[name] = (alias, data, pymupdf.Font(fontbuffer=data))
        else:
            base = _base14(name)
            _WRITING_FONTS[name] = (base, None, pymupdf.Font(base))
    return _WRITING_FONTS[name]


# --- rewriting -------------------------------------------------------------


def _replace_on_page(pymupdf, doc, page, hits, new_date):
    """Remove the glyphs of `hits` from the (unrotated) page and write the new
    dates in their place. Return the new strings, in the order of `hits`."""
    new_texts = [h.style.format(new_date) for h in hits]

    # 1. Remove the old glyphs. The redaction area is the date's box shrunk
    # across the text and pulled in at both ends, so that it cannot catch a
    # neighbouring line or the characters right before and after the date.
    for h in hits:
        x0, y0, x1, y1 = h.raw_rect
        horizontal = h.rotation in (0, 180)
        across = (y1 - y0) if horizontal else (x1 - x0)
        along = (x1 - x0) if horizontal else (y1 - y0)
        pull = 0.25 * along / max(len(h.text), 1)
        if horizontal:
            r = (x0 + pull, y0 + 0.25 * across, x1 - pull, y1 - 0.25 * across)
        else:
            r = (x0 + 0.25 * across, y0 + pull, x1 - 0.25 * across, y1 - pull)
        page.add_redact_annot(pymupdf.Rect(r), fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)

    # 2. The fonts to write with: installed copy of the original, or
    # Helvetica. Only now: applying the redactions drops unused resources.
    fonts = {}  # original font name -> (fontname on the page, pymupdf.Font)
    for name in sorted({h.font for h in hits}):
        alias, data, font = _writing_font(pymupdf, name)
        if data is not None:
            page.insert_font(fontname=alias, fontbuffer=data)
        fonts[name] = (alias, font)

    # 3. Write the new dates, as wide as the old ones.
    for h, text in zip(hits, new_texts):
        alias, font = fonts[h.font]
        natural = font.text_length(h.text, fontsize=h.size)
        scale = h.length / natural if natural > 0 and h.length > 0 else 1.0
        scale = min(max(scale, 0.3), 3.0)
        horizontal = h.rotation in (0, 180)
        morph = (pymupdf.Point(h.origin),
                 pymupdf.Matrix(scale, 1) if horizontal else pymupdf.Matrix(1, scale))
        c = h.color
        page.insert_text(h.origin, text, fontsize=h.size, fontname=alias,
                         rotate=h.rotation, morph=morph,
                         color=((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255))
    return new_texts


def _grey_render(pymupdf, page, scale):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                          colorspace=pymupdf.csGRAY, alpha=False, annots=True)
    return pix


def _verify(pymupdf, before_page, after_page, hits, new_texts, rotation_matrix):
    """Compare the page before and after: the same text apart from the dates,
    and the same picture outside the dates. Raise CartiglioError otherwise."""
    old_chars = Counter(before_page.get_text("text").replace("\n", "").replace(" ", ""))
    new_chars = Counter(after_page.get_text("text").replace("\n", "").replace(" ", ""))
    for h, text in zip(hits, new_texts):
        old_chars.subtract(h.text)
        old_chars.update(text)
    if +old_chars != +new_chars:
        raise CartiglioError(_("check failed: the text of the page changed "
                               "beyond the dates; the file was not saved"))

    w, h_ = before_page.rect.width, before_page.rect.height
    scale = min(2.0, math.sqrt(_VERIFY_PIXELS / max(w * h_, 1)))
    a = _grey_render(pymupdf, before_page, scale)
    b = _grey_render(pymupdf, after_page, scale)
    if (a.width, a.height) != (b.width, b.height):
        raise CartiglioError(_("check failed: the page size changed; the file was not saved"))
    # Blank out the dates (old and new) in both pictures, then compare.
    for hit in hits:
        x0, y0, x1, y1 = hit.raw_rect
        grow = 0.5 * hit.size
        r = _transform_rect((x0 - grow, y0 - grow, x1 + grow, y1 + grow), rotation_matrix)
        ir = pymupdf.IRect(math.floor(r[0] * scale), math.floor(r[1] * scale),
                           math.ceil(r[2] * scale), math.ceil(r[3] * scale))
        a.set_rect(ir, (255,))
        b.set_rect(ir, (255,))
    sa, sb, stride = a.samples_mv, b.samples_mv, a.stride
    changed = 0
    for row in range(a.height):
        ra, rb = sa[row * stride:(row + 1) * stride], sb[row * stride:(row + 1) * stride]
        if ra != rb:
            changed += sum(1 for p, q in zip(ra, rb) if abs(p - q) > _VERIFY_LEVEL)
            if changed > _VERIFY_TOLERANCE:
                raise CartiglioError(_("check failed: something besides the dates "
                                       "changed on the page; the file was not saved"))


# --- explicit line moves -------------------------------------------------------
#
# MuPDF 1.28 (PyMuPDF 1.28.0-1.28.2) mangles text positioning when it
# rewrites a page for a redaction: it drops TL (the leading) and turns Td into
# TD, so the lines that follow with T*, ' or " jump, often off the page, and
# the dates on them are not even removed. AutoCAD and Word title blocks use
# exactly those operators. So before the redaction the page's own line moves
# are made explicit ("0 -17 Td" instead of T* with a leading of 17), which
# draws the same page and does not depend on the leading at all. The
# before/after check still compares with the untouched original.

_DELIMS = b"()<>[]{}/%"
_SPACE = b" \t\r\n\f\x00"


def _num(value: float) -> bytes:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return b"0" if text in ("", "-0") else text.encode()


def _tokens(data: bytes):
    """Yield (start, end, kind, value) over a content stream; kind is "num",
    "op" or "other" (strings, names, arrays, dictionaries, inline images)."""
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c in _SPACE:
            i += 1
        elif c == 0x25:  # % comment
            while i < n and data[i] not in b"\r\n":
                i += 1
        elif c == 0x28:  # ( literal string, nested parentheses and escapes
            start, depth = i, 0
            while i < n:
                ch = data[i]
                if ch == 0x5C:
                    i += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            yield start, i, "other", None
        elif c == 0x3C and data[i + 1:i + 2] != b"<":  # <hex string>
            start = i
            i = data.find(b">", i) + 1 or n
            yield start, i, "other", None
        elif c in b"<>[]{}":  # dictionary and array brackets: no operators inside
            yield i, i + (2 if data[i:i + 2] in (b"<<", b">>") else 1), "other", None
            i += 2 if data[i:i + 2] in (b"<<", b">>") else 1
        else:
            start = i
            i += 1
            while i < n and data[i] not in _SPACE and data[i] not in _DELIMS:
                i += 1
            word = data[start:i]
            if c == 0x2F:  # /Name
                yield start, i, "other", None
                continue
            try:
                yield start, i, "num", float(word)
                continue
            except ValueError:
                pass
            yield start, i, "op", word
            if word == b"ID":  # inline image data: skip to EI
                j = i + 1
                while True:
                    j = data.find(b"EI", j)
                    if j < 0:
                        i = n
                        break
                    after = data[j + 2:j + 3]
                    if data[j - 1:j] in _SPACE and (not after or after in _SPACE):
                        i = j
                        break
                    j += 2


def explicit_line_moves(data: bytes, leading: float = 0.0):
    """Rewrite T*, ', " and TD in a content stream as explicit Td moves.
    Return (new data or None if unchanged, leading at the end)."""
    out, last = [], 0
    operands = []          # (start, end, kind, value) since the last operator
    stack = []             # leading saved by q, restored by Q
    changed = False
    for start, end, kind, value in _tokens(data):
        if kind != "op":
            operands.append((start, end, kind, value))
            continue
        op = value
        new = None
        if op == b"q":
            stack.append(leading)
        elif op == b"Q":
            leading = stack.pop() if stack else 0.0
        elif op == b"TL" and operands and operands[-1][2] == "num":
            leading = operands[-1][3]
        elif op == b"TD" and len(operands) >= 2 and operands[-1][2] == operands[-2][2] == "num":
            tx, ty = operands[-2][3], operands[-1][3]
            leading = -ty
            new = (operands[-2][0], _num(-ty) + b" TL " + _num(tx) + b" " + _num(ty) + b" Td")
        elif op == b"T*":
            new = (start, b"0 " + _num(-leading) + b" Td")
        elif op == b"'" and operands:
            s = operands[-1]
            new = (s[0], b"0 " + _num(-leading) + b" Td " + data[s[0]:s[1]] + b" Tj")
        elif op == b'"' and len(operands) >= 3 and operands[-3][2] == operands[-2][2] == "num":
            aw, ac, s = operands[-3], operands[-2], operands[-1]
            new = (aw[0], data[aw[0]:aw[1]] + b" Tw " + data[ac[0]:ac[1]] + b" Tc 0 "
                   + _num(-leading) + b" Td " + data[s[0]:s[1]] + b" Tj")
        if new is not None:
            out.append(data[last:new[0]])
            out.append(new[1])
            last = end
            changed = True
        operands = []
    if not changed:
        return None, leading
    out.append(data[last:])
    return b"".join(out), leading


def _explicit_line_moves(doc, page) -> None:
    """Apply explicit_line_moves to the content streams of a page, in order
    (the leading carries over from one stream to the next)."""
    leading = 0.0
    for xref in page.get_contents():
        data = doc.xref_stream(xref)
        if not data:
            continue
        new, leading = explicit_line_moves(data, leading)
        if new is not None:
            doc.update_stream(xref, new)


def _rewrite(pymupdf, data, analysis, selection, new_date, verify=True, pages=None,
             progress=None):
    """Open `data`, replace the selected dates page by page, check each page.
    `pages` limits the work to those page numbers (previews).
    Return (document, before_document, hits, new_texts); the caller closes both."""
    hits = [d for d in analysis.dates if d.index in selection
            and (pages is None or d.page in pages)]
    if not hits:
        raise NothingToChange(str(analysis.path))
    by_page = {}
    for h in hits:
        by_page.setdefault(h.page, []).append(h)
    doc = _open(pymupdf, data)
    before = _open(pymupdf, data)
    written = {}
    try:
        for done, (number, page_hits) in enumerate(sorted(by_page.items()), 1):
            page = doc[number]
            _explicit_line_moves(doc, page)
            rotation = page.rotation
            rotation_matrix = tuple(page.rotation_matrix)
            page.set_rotation(0)
            try:
                new_texts = _replace_on_page(pymupdf, doc, page, page_hits, new_date)
            finally:
                page.set_rotation(rotation)
            if verify:
                _verify(pymupdf, before[number], doc[number], page_hits, new_texts,
                        rotation_matrix)
            written.update(zip((h.index for h in page_hits), new_texts))
            if progress:
                progress(done / len(by_page))
    except BaseException:
        doc.close()
        before.close()
        raise
    return doc, before, hits, [written[h.index] for h in hits]


def redate_file(analysis: Analysis, dest: Path, new_date: date, selection=None,
                overwrite: bool = False, progress=None) -> Result:
    """Write analysis.path to dest with the selected dates (default: see
    Analysis.default_selection) replaced by new_date.

    Raises FileExistsError when dest exists and overwrite is False,
    NothingToChange when no date is selected, CartiglioError for unsupported
    input or a failed check, OSError on I/O problems."""
    dest = Path(dest)
    if dest.exists() and not overwrite:
        raise FileExistsError(str(dest))
    if Path(dest).resolve() == Path(analysis.path).resolve():
        raise CartiglioError(_("the output would overwrite the original"))
    selection = analysis.default_selection() if selection is None else set(selection)
    if not selection:
        raise NothingToChange(str(analysis.path))
    pymupdf = load_engine()
    data = read_bytes(analysis.path, progress, 0.3)
    doc, before, hits, _texts = _rewrite(
        pymupdf, data, analysis, selection, new_date,
        progress=(lambda f: progress(0.3 + 0.5 * f)) if progress else None)
    if progress:
        progress(0.8)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        # A full, compact save: the rewritten page is compressed and the
        # orphaned objects dropped, so the file is no larger than before.
        # (garbage=3 would also merge duplicate objects: no smaller here, and
        # half a minute on a long report.)
        doc.save(tmp, garbage=2, deflate=True, use_objstms=1,
                 encryption=pymupdf.PDF_ENCRYPT_KEEP)
        os.replace(tmp, dest)
    finally:
        doc.close()
        before.close()
        try:
            tmp.unlink()
        except OSError:
            pass
    if progress:
        progress(1.0)
    return Result(dest, len(hits))


def preview_png(analysis: Analysis, new_date: date, selection=None, width_px: int = 900,
                page: int = 0):
    """Pictures of the title block of one page before and after:
    (before_png, after_png), with the dates to change outlined. after_png is
    None when nothing is selected there. Nothing is written to disk."""
    pymupdf = load_engine()
    selection = analysis.default_selection() if selection is None else set(selection)
    data = read_bytes(analysis.path)
    clip = _preview_clip(analysis, selection, page)
    scale = width_px / max(clip[2] - clip[0], 1)
    rect = pymupdf.Rect(clip)

    def shot(page, _hits):
        # Outline and number every date of this page as the GUI lists them:
        # red for those to change, grey for the others. Preview only: this
        # document is never saved.
        for h in [d for d in analysis.dates if d.page == page.number]:
            color = (0.86, 0.15, 0.15) if h.index in selection else (0.55, 0.55, 0.55)
            page.draw_rect(_unrotate(pymupdf, page, h.rect), color=color,
                           width=max(0.6, 1.2 / scale), overlay=True)
            # as an exponent at the top right of the box, upright on screen
            size = max(4.0, 0.7 * min(h.rect[2] - h.rect[0], h.rect[3] - h.rect[1]))
            spot = (pymupdf.Point(h.rect[2] + 0.15 * size, h.rect[1] + 0.6 * size)
                    * page.derotation_matrix)
            page.insert_text(spot, str(h.index), fontsize=size, fontname="hebo",
                             color=color, rotate=page.rotation, overlay=True)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect,
                               alpha=False).tobytes("png")

    marked = [d for d in analysis.dates if d.index in selection and d.page == page]
    doc = _open(pymupdf, data)
    try:
        before = shot(doc[page], marked)
    finally:
        doc.close()
    if not marked:
        return before, None
    doc, original, hits, _texts = _rewrite(pymupdf, data, analysis, selection, new_date,
                                           verify=False, pages={page})
    try:
        after = shot(doc[page], hits)
    finally:
        doc.close()
        original.close()
    return before, after


def _unrotate(pymupdf, page, rect):
    """A rect in displayed coordinates -> the page's own, for drawing on it."""
    return pymupdf.Rect(rect) * page.derotation_matrix


def _preview_clip(analysis: Analysis, selection, page: int = 0):
    """What to show in the preview: the dates of the title block with some
    room around them (a whole cover would be too small to read)."""
    shown = [d for d in analysis.dates
             if d.page == page and (d.in_block or d.index in selection)]
    if page == 0:
        W, H = analysis.page_size
    else:  # a later sheet: its size is not kept, the dates are enough
        W = H = float("inf")
    if not shown:
        x0, y0, x1, y1 = analysis.zone
    else:
        x0 = min(d.rect[0] for d in shown)
        y0 = min(d.rect[1] for d in shown)
        x1 = max(d.rect[2] for d in shown)
        y1 = max(d.rect[3] for d in shown)
        pad = max(40.0, 0.15 * max(x1 - x0, y1 - y0))
        x0, y0, x1, y1 = x0 - 3 * pad, y0 - pad, x1 + pad, y1 + pad
    return (max(0.0, x0), max(0.0, y0), min(W, x1), min(H, y1))


# --- batches ---------------------------------------------------------------


def iter_pdf(paths):
    """Expand files/folders into (pdf, root) pairs. Folders are scanned
    recursively; root is the folder given (for files, their own folder), so
    that the output can mirror the tree below it."""
    found = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            found.extend((x, p) for x in sorted(p.rglob("*"))
                         if x.is_file() and x.suffix.lower() == ".pdf")
        elif p.is_file():
            found.append((p, None))
    return found


def date_label(value: date) -> str:
    """16-10-2026: safe in file names everywhere."""
    return value.strftime("%d-%m-%Y")


def output_path(src: Path, root: Path | None, new_date: date, out_dir: Path | None = None) -> Path:
    """Where the changed copy of src goes, keeping its name:

    - a file from a folder that was given:  <folder> - 16-10-2026/<same subpath>
    - a file given on its own:              <its folder>/Cartiglio 16-10-2026/<name>
    - with out_dir:                         <out_dir>/<subpath below the folder given, or name>
    """
    src = Path(src)
    label = date_label(new_date)
    rel = src.relative_to(root) if root is not None else Path(src.name)
    if out_dir is not None:
        return Path(out_dir) / rel
    if root is not None:
        root = Path(root)
        return root.with_name(f"{root.name} - {label}") / rel
    return src.parent / f"{APP_NAME} {label}" / src.name


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


# ---------------------------------------------------------------------------
# Preferences (INI file in the per-user configuration directory)
# ---------------------------------------------------------------------------


def config_dir() -> Path:
    """Per-user configuration directory, following platform conventions:
    $XDG_CONFIG_HOME on Linux (Flatpak points it inside the sandbox),
    %LOCALAPPDATA% on Windows."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "cartiglio"


class Settings:
    """Tiny persistent key/value store (settings.ini). No file means defaults."""

    DEFAULTS = {
        "language": "auto",            # auto (desktop language) | it | en
        "color_scheme": "auto",        # auto (follow the system) | light | dark
        # Windows: the system title bar (and so Snap) rather than GTK's
        "system_title_bar": "true",
        "check_updates": "true",       # Windows: look for a new release daily
        "last_update_check": "",       # ISO date of the last automatic check
    }

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_dir() / "settings.ini"
        self._cp = configparser.ConfigParser()
        self._cp["general"] = dict(self.DEFAULTS)
        try:
            self._cp.read(self.path, encoding="utf-8")
        except (OSError, configparser.Error):
            pass

    def get(self, key: str) -> str:
        return self._cp["general"].get(key, self.DEFAULTS.get(key, ""))

    def get_bool(self, key: str) -> bool:
        return self.get(key).strip().lower() in ("1", "true", "yes", "on")

    def set(self, key: str, value) -> None:
        if isinstance(value, bool):
            value = "true" if value else "false"
        self._cp["general"][key] = str(value)
        self.save()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                self._cp.write(f)
        except OSError:
            pass  # preferences are a convenience, never fatal


# ---------------------------------------------------------------------------
# Update check (GitHub Releases API, stdlib only)
#
# Used by the Windows builds only: on Linux updates come from the package
# manager / Flatpak (GNOME Software), as the platform conventions require.
# ---------------------------------------------------------------------------


class UpdateError(Exception):
    """Problem while talking to GitHub. `reached` tells whether the server
    answered at all (HTTP error) or the network/TLS layer failed."""

    def __init__(self, message: str, reached: bool = False):
        super().__init__(message)
        self.reached = reached


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.0' -> (1, 2, 0). Non-numeric components count as 0."""
    parts = []
    for piece in text.strip().lstrip("vV").split("."):
        m = re.match(r"\d+", piece)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_installed_build() -> bool:
    """True for the Inno Setup installation (uninstaller next to the exe),
    False for the portable zip and for source checkouts."""
    return (sys.platform == "win32" and is_frozen()
            and (Path(sys.executable).parent / "unins000.exe").is_file())


def pick_asset(assets, installed: bool):
    """The release asset matching this build: installer or portable zip.
    A released version (never a CI "dev" build), from this project's own
    release downloads; the installer, which is run, only with the SHA-256
    GitHub gives for it."""
    version = r"v\d+(?:\.\d+){1,3}"
    pattern = (rf"^cartiglio-setup-{version}-windows-x64\.exe$" if installed
               else rf"^cartiglio-{version}-windows-x64-portable\.zip$")
    for a in assets:
        if (re.match(pattern, a.get("name", ""), re.IGNORECASE)
                and a.get("url", "").startswith(DOWNLOADS_URL)
                and (a.get("sha256") or not installed)):
            return a
    return None


UPDATE_FOLDER_PREFIX = "cartiglio-update-"


def update_download_folder() -> Path:
    """A new folder of its own for an installer, in the temporary folder."""
    import tempfile
    return Path(tempfile.mkdtemp(prefix=UPDATE_FOLDER_PREFIX))


def clean_update_downloads() -> None:
    """The installers of earlier updates, gone (Setup is done with them by
    the time the app starts again)."""
    import shutil
    import tempfile
    for folder in Path(tempfile.gettempdir()).glob(UPDATE_FOLDER_PREFIX + "*"):
        shutil.rmtree(folder, ignore_errors=True)


def _ssl_context():
    import ssl
    ctx = ssl.create_default_context()
    # The frozen Windows build ships a CA bundle: MSYS2's OpenSSL cannot
    # always see the Windows certificate store.
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "certs" / "ca-bundle.crt"
    if bundled.is_file():
        try:
            ctx.load_verify_locations(cafile=str(bundled))
        except (ssl.SSLError, OSError):
            pass
    return ctx


def _http_get(url: str, timeout: float = 15.0):
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": f"cartiglio/{__version__}",
        "Accept": "application/vnd.github+json",
    })
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def fetch_latest_release(timeout: float = 15.0) -> dict:
    """Return {"version", "tag", "url", "notes", "assets": [{"name", "url", "size"}]}."""
    import json
    import urllib.error
    try:
        with _http_get(LATEST_RELEASE_API, timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise UpdateError(_("GitHub answered {code}").format(code=e.code),
                          reached=True) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError(str(getattr(e, "reason", e))) from None
    tag = str(data.get("tag_name") or "")
    if not tag:
        raise UpdateError(_("unexpected answer from the server"), reached=True)
    return {
        "version": tag.lstrip("vV"),
        "tag": tag,
        "url": data.get("html_url") or RELEASES_URL,
        "notes": data.get("body") or "",
        "assets": [
            {"name": a.get("name", ""), "url": a.get("browser_download_url", ""),
             "size": int(a.get("size") or 0),
             # GitHub's checksum of the file, "sha256:<hex>"
             "sha256": str(a.get("digest") or "").removeprefix("sha256:")
             if str(a.get("digest") or "").startswith("sha256:") else ""}
            for a in data.get("assets", [])
        ],
    }


def download_file(url: str, dest: Path, progress=None, cancelled=None, sha256: str = "", size: int = 0) -> Path:
    """Download url to dest, checked against its size and its SHA-256: a file
    that does not match is removed, and without a SHA-256 nothing is
    downloaded. progress(done, total) is called along the way; cancelled is
    an optional threading.Event."""
    import hashlib
    import urllib.error
    if not sha256:
        raise UpdateError(_("the release gives no checksum for the installer: not downloaded"), reached=True)
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    try:
        with _http_get(url, timeout=30) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or size or 0)
            done = 0
            while True:
                if cancelled is not None and cancelled.is_set():
                    raise UpdateError(_("cancelled"), reached=True)
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        # a connection cut short ends the reading without an error
        if (size and done != size) or digest.hexdigest().lower() != sha256.lower():
            raise UpdateError(_("the file downloaded does not match the release: not started"), reached=True)
        tmp.replace(dest)
        return dest
    except urllib.error.HTTPError as e:
        raise UpdateError(_("download failed ({code})").format(code=e.code),
                          reached=True) from None
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(str(getattr(e, "reason", e))) from None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def run_check_update() -> int:
    """`--check-update`: print the latest release. Exit 0 when GitHub answered
    (up to date or not), 3 when the network/TLS layer failed."""
    print(_("Installed version: {version}").format(version=__version__))
    try:
        rel = fetch_latest_release()
    except UpdateError as e:
        print(_("Update check failed: {error}").format(error=e), file=sys.stderr)
        return 0 if e.reached else 3
    if is_newer(rel["version"]):
        print(_("Available: {version}  ({url})").format(version=rel["version"], url=rel["url"]))
    else:
        print(_("Latest version: {version} (up to date)").format(version=rel["version"]))
    return 0


# ---------------------------------------------------------------------------
# Windows shell integration (per-user registry, no admin rights)
#
# One context-menu entry on PDF files and on folders: files and whole
# deliveries are both things one re-dates. Nothing else is registered.
# The installer writes the same keys (HKLM when installed for all users);
# these functions cover the portable build.
# ---------------------------------------------------------------------------

_VERB_KEYS = (
    rf"Software\Classes\SystemFileAssociations\.pdf\shell\{WIN_VERB}",
    rf"Software\Classes\Directory\shell\{WIN_VERB}",
)


def _launch_command() -> str:
    """Command line that opens files with this program (registry format)."""
    if is_frozen():
        return f'"{sys.executable}" --gui "%1"'
    return f'"{sys.executable}" "{Path(__file__).resolve()}" --gui "%1"'


def _shell_notify() -> None:
    try:
        import ctypes
        # SHCNE_ASSOCCHANGED, SHCNF_IDLIST
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except (AttributeError, OSError):
        pass


def _reg_delete_tree(root, path: str) -> None:
    import winreg
    try:
        with winreg.OpenKey(root, path) as k:
            subs = []
            while True:
                try:
                    subs.append(winreg.EnumKey(k, len(subs)))
                except OSError:
                    break
    except FileNotFoundError:
        return
    for sub in subs:
        _reg_delete_tree(root, f"{path}\\{sub}")
    try:
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def win_register() -> None:
    """Add the context-menu verb on PDF files and folders under
    HKEY_CURRENT_USER. It is shown whatever the default PDF app is."""
    import winreg

    def put(path, value="", name=""):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    for key in _VERB_KEYS:
        put(key, _("Change title block dates with Cartiglio"))
        if is_frozen():
            put(key, f"{sys.executable},0", "Icon")
        put(key, "Player", "MultiSelectModel")  # no 15-files prompt on multi-select
        put(rf"{key}\command", _launch_command())
    _shell_notify()


def win_unregister() -> None:
    """Remove what win_register() wrote (current user only)."""
    import winreg
    for key in _VERB_KEYS:
        _reg_delete_tree(winreg.HKEY_CURRENT_USER, key)
    _shell_notify()


def win_is_registered() -> bool:
    """True when the context-menu verb exists, for this user or machine-wide."""
    import winreg
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            winreg.CloseKey(winreg.OpenKey(root, rf"{_VERB_KEYS[0]}\command"))
            return True
        except OSError:
            continue
    return False


def win_allow_foreground() -> None:
    """Let the next process that asks take the foreground. Windows grants
    that right only to the process the user just launched (us): when we hand
    our files to an already running instance, it needs it to raise its window."""
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except (AttributeError, OSError):
        pass


def win_bring_to_front(title: str) -> None:
    """Restore (if minimized) and raise this process's top-level window
    called `title`."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return
    pid = os.getpid()
    buf = ctypes.create_unicode_buffer(256)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == title:
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return 0  # found: stop enumerating
        return 1

    user32.EnumWindows(visit, 0)


def win_dark_titlebars(dark: bool) -> None:
    """Ask DWM to paint the native title bars of this process dark or light
    (Windows 10 1809+; silently ignored elsewhere)."""
    try:
        import ctypes
        from ctypes import wintypes
        user32, dwmapi = ctypes.windll.user32, ctypes.windll.dwmapi
    except (AttributeError, OSError):
        return
    pid = os.getpid()
    value = ctypes.c_int(1 if dark else 0)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20H1+ / 1809)
                if dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                                ctypes.sizeof(value)) == 0:
                    break
        return 1

    user32.EnumWindows(visit, 0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_cli(paths, new_date: date, overwrite: bool, out_dir, every_date: bool,
            whole_page: bool, dry_run: bool, all_pages=None) -> int:
    files = iter_pdf(paths)
    if not files:
        print(_("No PDF files found."), file=sys.stderr)
        return 1
    try:
        load_engine()
    except CartiglioError as e:
        print(f"ERR   {e}", file=sys.stderr)
        return 2
    n_ok = n_skip = n_err = 0
    for src, root in files:
        try:
            analysis = analyse_file(src, all_pages=all_pages)
            if whole_page:
                for d in analysis.dates:
                    d.in_block = d.in_block or d.page == 0
            selection = ({d.index for d in analysis.block_dates} if every_date
                         else analysis.default_selection())
            found = describe(analysis, selection)
            if dry_run:
                print(f"{src}  ({found})")
                for d in analysis.dates:
                    if d.page > 0 and not analysis.all_pages:
                        continue
                    mark = "x" if d.index in selection else ("-" if d.in_block else " ")
                    where = _("title block") if d.in_block else _("elsewhere")
                    page = _("page {n}").format(n=d.page + 1)
                    print(f"   [{mark}] {d.index:3}  {d.text:12} {page:10} {where}")
                continue
            dest = output_path(src, root, new_date, out_dir)
            result = redate_file(analysis, dest, new_date, selection, overwrite)
            print(f"OK    {result.dest}  ({found})")
            n_ok += 1
        except FileExistsError as e:
            print(_("SKIP  {file}  (already exists: {name}; use --overwrite)").format(
                file=src, name=Path(str(e)).name))
            n_skip += 1
        except NothingToChange:
            print(_("SKIP  {file}  ({reason})").format(file=src, reason=describe(analysis)))
            n_skip += 1
        except (CartiglioError, OSError) as e:
            print(f"ERR   {src}  ({e})", file=sys.stderr)
            n_err += 1
    if not dry_run:
        print("\n" + _("Changed: {ok}  Skipped: {skipped}  Errors: {errors}").format(
            ok=n_ok, skipped=n_skip, errors=n_err))
    return 0 if n_err == 0 else 1


def _date_arg(text: str) -> date:
    try:
        return parse_user_date(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            _("not a valid date: {value} (use dd/mm/yyyy)").format(value=text)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cartiglio",
        description=_("Change the dates in the title block of PDF drawings and "
                      "documents, many files at once."),
        epilog=_("Without arguments the graphical interface (GTK 4) starts. "
                 "Examples: cartiglio --date 16/10/2026 folder/ | "
                 "cartiglio --dry-run drawing.pdf"),
    )
    parser.add_argument("paths", nargs="*", metavar=_("FILE_OR_FOLDER"),
                        help=_("PDF files or folders to scan (recursive)"))
    parser.add_argument("--date", type=_date_arg, default=None, metavar="DD/MM/YYYY",
                        help=_("the new date (default: today)"))
    parser.add_argument("--out", type=Path, default=None, metavar=_("FOLDER"),
                        help=_("write the changed files here, keeping the "
                               "sub-folders (default: a new folder next to the "
                               "originals, named after the date)"))
    parser.add_argument("--all", action="store_true", dest="every_date",
                        help=_("change every date in the title block, older "
                               "ones too (default: only those equal to the most "
                               "recent, so earlier revisions keep theirs)"))
    parser.add_argument("--whole-page", action="store_true",
                        help=_("treat every date on the first page as part of "
                               "the title block"))
    pages = parser.add_mutually_exclusive_group()
    pages.add_argument("--all-pages", action="store_const", const=True, dest="all_pages",
                       help=_("also change the title blocks of the later pages "
                              "(default: only when every sheet has one, as in "
                              "a set of single-line diagrams)"))
    pages.add_argument("--first-page", action="store_const", const=False, dest="all_pages",
                       help=_("only change the first page"))
    parser.add_argument("--dry-run", action="store_true",
                        help=_("only list the dates found, write nothing"))
    parser.add_argument("--overwrite", action="store_true",
                        help=_("overwrite files already in the output folder"))
    parser.add_argument("--gui", action="store_true",
                        help=_("force the graphical interface"))
    parser.add_argument("--check-update", action="store_true",
                        help=_("check whether a newer version exists and exit"))
    parser.add_argument("--register", action="store_true",
                        help=_("(Windows) add the entry to the File Explorer "
                               "context menu for the current user and exit"))
    parser.add_argument("--unregister", action="store_true",
                        help=_("(Windows) remove the context-menu entry and exit"))
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    return parser


# ---------------------------------------------------------------------------
# GTK 4 GUI
# ---------------------------------------------------------------------------

# Plain GTK 4 (no libadwaita): GTK's own named colours.
_CSS = b"""
.dropzone {
    border: 2px dashed alpha(currentColor, 0.25);
    border-radius: 12px;
}
.dropzone.hover {
    border-color: @theme_selected_bg_color;
    background: alpha(@theme_selected_bg_color, 0.08);
}
.app-banner {
    background: alpha(@theme_selected_bg_color, 0.14);
    border-radius: 8px;
    padding: 6px 6px 6px 12px;
}
.preview-frame {
    background: white;
}
"""

# libadwaita: the accent colour it exports; messages are toasts, no banner.
_CSS_ADW = b"""
.dropzone {
    border: 2px dashed alpha(currentColor, 0.25);
    border-radius: 12px;
}
.dropzone.hover {
    border-color: @accent_bg_color;
    background: alpha(@accent_bg_color, 0.08);
}
/* The drop zone is an AdwStatusPage; even "compact" it would take more than
   half of the default window, leaving the results list no room. */
.dropzone > scrolledwindow > viewport > box {
    margin: 14px 12px;
}
.dropzone .icon {
    -gtk-icon-size: 56px;
    margin-bottom: 8px;
}
.dropzone .title {
    font-size: 15pt;
}
.preview-frame {
    background: white;
}
"""


def run_gui(argv, settings: Settings) -> int:
    is_win = sys.platform == "win32"
    # The Windows title bar (system decorations): GTK honours GTK_CSD only
    # before it initialises, hence the environment variable set up front.
    use_csd = not (is_win and settings.get_bool("system_title_bar"))
    if not use_csd:
        os.environ["GTK_CSD"] = "0"
    if is_win:
        # The GL renderers spend ~1 s compiling shaders before the first
        # frame on Windows; this UI needs none of their features.
        os.environ.setdefault("GSK_RENDERER", "cairo")
    theme = None  # ThemeManager, created once GTK is up (App.do_startup)

    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, GLib, Gtk, Pango
    except (ImportError, ValueError):
        print(
            _("GTK 4 / PyGObject not available. Install:") + "\n"
            "  Debian/Ubuntu:  sudo apt install python3-gi gir1.2-gtk-4.0 "
            "gir1.2-adw-1\n"
            "  Fedora:         sudo dnf install python3-gobject gtk4 libadwaita\n"
            "  Arch:           sudo pacman -S python-gobject gtk4 libadwaita\n"
            "  Windows(MSYS2 UCRT64): pacman -S mingw-w64-ucrt-x86_64-gtk4 "
            "mingw-w64-ucrt-x86_64-libadwaita mingw-w64-ucrt-x86_64-python-gobject\n"
            + _("or download the portable build from the GitHub Releases.") + "\n"
            + _("Headless use:  cartiglio --date DD/MM/YYYY FILE_OR_FOLDER..."),
            file=sys.stderr,
        )
        return 2

    # libadwaita is the GNOME platform library: it supplies the current HIG
    # widgets and follows the system light/dark preference by itself (GTK
    # deprecated gtk-application-prefer-dark-theme in 4.20). Also on Windows,
    # for the content only when the window keeps the system title bar (an
    # AdwApplicationWindow always draws its own, and Snap needs Windows'
    # own: GTK's imitation of it does not work).
    # CARTIGLIO_NO_ADW=1 forces the plain GTK path (testing aid).
    Adw = None
    if not os.environ.get("CARTIGLIO_NO_ADW"):
        try:
            gi.require_version("Adw", "1")
            from gi.repository import Adw
            # AdwDialog (About, Preferences) needs 1.5, AdwToolbarView 1.4:
            # older libadwaita takes the plain GTK 4 path below instead.
            if (Adw.MAJOR_VERSION, Adw.MINOR_VERSION) < (1, 5):
                Adw = None
        except (ImportError, ValueError):
            Adw = None
    use_adw = Adw is not None
    _mark("gtk-imported")

    import queue
    import threading

    has_filedialog = Gtk.check_version(4, 10, 0) is None
    in_flatpak = not is_win and Path("/.flatpak-info").is_file()

    # --- small helpers ----------------------------------------------------

    def set_margins(widget, value=None, **sides):
        for side in ("top", "bottom", "start", "end"):
            v = sides.get(side, value)
            if v is not None:
                getattr(widget, f"set_margin_{side}")(v)

    def close_on_escape(window):
        ctl = Gtk.EventControllerKey()

        def on_key(_ctl, keyval, _code, _state):
            if keyval == Gdk.KEY_Escape:
                window.close()
                return True
            return False
        ctl.connect("key-pressed", on_key)
        window.add_controller(ctl)

    def init_dialog(win):
        """Common set-up of secondary windows: GTK header bar when using
        client-side decorations, Escape closes, native title bar follows the
        colour scheme on Windows."""
        if use_csd:
            win.set_titlebar(Gtk.HeaderBar())
        close_on_escape(win)
        theme.watch_window(win)

    def button_row(*buttons):
        row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        row.set_margin_top(8)
        for b in buttons:
            row.append(b)
        return row

    def open_uri(uri, parent=None):
        if has_filedialog:  # GTK >= 4.10
            Gtk.UriLauncher.new(uri).launch(parent, None, None)
        elif is_win:
            os.startfile(uri)
        else:
            Gio.AppInfo.launch_default_for_uri(uri, None)

    def texture(png: bytes):
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))

    def host_paths(paths):
        """Inside a Flatpak, files dropped or picked through the portals may
        arrive as /run/user/UID/doc/ID/... paths. The output is written next
        to the source, so map them back to the real location whenever the
        sandbox can write there (org.freedesktop.portal.Documents.GetHostPaths)."""
        if not in_flatpak:
            return paths
        doc_root = Path(GLib.get_user_runtime_dir()) / "doc"
        rels = {}
        for p in paths:
            try:
                rels[p] = Path(p).relative_to(doc_root)
            except ValueError:
                pass
        ids = sorted({r.parts[0] for r in rels.values() if len(r.parts) >= 2})
        if not ids:
            return paths
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = bus.call_sync(
                "org.freedesktop.portal.Documents",
                "/org/freedesktop/portal/documents",
                "org.freedesktop.portal.Documents", "GetHostPaths",
                GLib.Variant("(as)", (ids,)), GLib.VariantType("(a{say})"),
                Gio.DBusCallFlags.NONE, 3000, None)
            mapping = reply.unpack()[0]
        except GLib.Error:
            return paths
        out = []
        for p in paths:
            rel = rels.get(p)
            host = mapping.get(rel.parts[0]) if rel is not None and len(rel.parts) >= 2 else None
            if host is None:
                out.append(p)
                continue
            if isinstance(host, list):
                host = bytes(host)
            real = Path(os.fsdecode(bytes(host).rstrip(b"\0"))).joinpath(*rel.parts[2:])
            target_dir = real if real.is_dir() else real.parent
            out.append(str(real) if real.exists() and os.access(target_dir, os.W_OK) else p)
        return out

    # --- colour scheme ----------------------------------------------------

    class ThemeManager:
        """Light/dark colour scheme: explicit, or automatic following the
        system. With libadwaita that is AdwStyleManager's job, the Windows
        title bar following it; on plain GTK 4 (no libadwaita) the preference
        is read by hand from the personalization key / the freedesktop
        settings portal."""

        def __init__(self):
            self.dark = False
            self._portal = None
            self._poll_id = 0
            self._watching = False
            self.apply()

        def apply(self):
            mode = settings.get("color_scheme")
            if use_adw:
                # libadwaita tracks the system preference itself, settings
                # portal included: DEFAULT means "follow the system".
                manager = Adw.StyleManager.get_default()
                manager.set_color_scheme({
                    "dark": Adw.ColorScheme.FORCE_DARK,
                    "light": Adw.ColorScheme.FORCE_LIGHT,
                }.get(mode, Adw.ColorScheme.DEFAULT))
                self.dark = manager.get_dark()
                if is_win and not self._watching:
                    # the system title bar follows libadwaita's shade
                    self._watching = True
                    manager.connect("notify::dark", self._on_adw_dark)
                if is_win:
                    win_dark_titlebars(self.dark)
                return
            if mode == "dark":
                dark = True
            elif mode == "light":
                dark = False
            else:
                dark = self._system_prefers_dark()
            self._set_dark(dark)
            if is_win:  # Windows gives no change notification here: poll cheaply
                if mode == "auto" and not self._poll_id:
                    self._poll_id = GLib.timeout_add_seconds(3, self._poll)
                elif mode != "auto" and self._poll_id:
                    GLib.source_remove(self._poll_id)
                    self._poll_id = 0

        def _on_adw_dark(self, manager, _pspec):
            self.dark = manager.get_dark()
            win_dark_titlebars(self.dark)

        def _poll(self):
            dark = self._system_prefers_dark()
            if dark != self.dark:
                self._set_dark(dark)
            return True  # keep polling

        def _set_dark(self, dark):
            self.dark = bool(dark)
            gtk_settings = Gtk.Settings.get_default()
            if gtk_settings is not None:
                gtk_settings.set_property("gtk-application-prefer-dark-theme", self.dark)
            if is_win:
                win_dark_titlebars(self.dark)

        def watch_window(self, win):
            """Paint the native title bar of a new window in the right shade."""
            if is_win:
                win.connect("map", lambda _w: win_dark_titlebars(self.dark))

        def _system_prefers_dark(self):
            if is_win:
                import winreg
                try:
                    with winreg.OpenKey(
                            winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                        return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
                except OSError:
                    return False
            # org.freedesktop.portal.Settings works inside and outside Flatpak
            try:
                if self._portal is None:
                    self._portal = Gio.DBusProxy.new_for_bus_sync(
                        Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                        "org.freedesktop.portal.Desktop",
                        "/org/freedesktop/portal/desktop",
                        "org.freedesktop.portal.Settings", None)
                    self._portal.connect("g-signal", self._on_portal_signal)
                reply = self._portal.call_sync(
                    "Read",
                    GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
                    Gio.DBusCallFlags.NONE, 1000, None)
                value = reply.unpack()[0]
                while isinstance(value, GLib.Variant):
                    value = value.unpack()
                return int(value) == 1  # 0 no preference, 1 dark, 2 light
            except (GLib.Error, TypeError, ValueError):
                return False

        def _on_portal_signal(self, _proxy, _sender, signal, params):
            if signal != "SettingChanged" or settings.get("color_scheme") != "auto":
                return
            namespace, key = params.unpack()[:2]
            if (namespace, key) == ("org.freedesktop.appearance", "color-scheme"):
                self.apply()

    # --- one file in the list ---------------------------------------------

    class FileRow(Gtk.ListBoxRow):
        """One PDF: queued → analysing → dates found (to review) → changing →
        outcome. The dates can be reviewed one by one under "Dates"."""

        def __init__(self, src, root, window):
            super().__init__(activatable=False)  # activatable once written
            self.src, self.root, self._win = Path(src), root, window
            self.analysis = None
            self.selection = set()
            self.state = "queued"   # queued | analysing | ready | empty | busy | done | error
            self.dest = None
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            set_margins(box, top=6, bottom=6, start=10, end=10)
            self.icon = Gtk.Image.new_from_icon_name("content-loading-symbolic")
            self.icon.add_css_class("dim-label")
            self.spinner = Gtk.Spinner()
            self.stack = Gtk.Stack(valign=Gtk.Align.CENTER)
            self.stack.add_named(self.icon, "icon")
            self.stack.add_named(self.spinner, "spinner")
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                            valign=Gtk.Align.CENTER)
            name = Gtk.Label(label=self.src.name, xalign=0.0)
            name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            self.status = Gtk.Label(label=_("Queued"), xalign=0.0)
            self.status.set_ellipsize(Pango.EllipsizeMode.END)
            self.status.add_css_class("dim-label")
            self.bar = Gtk.ProgressBar(visible=False)
            self.bar.set_margin_top(4)
            for w in (name, self.status, self.bar):
                texts.append(w)
            self.dates_btn = Gtk.ToggleButton(label=_("Dates"), valign=Gtk.Align.CENTER,
                                              visible=False,
                                              tooltip_text=_("Choose which dates to change"))
            self.dates_btn.add_css_class("flat")
            self.dates_btn.connect("toggled", lambda b: self.details.set_reveal_child(
                b.get_active()))
            self.preview_btn = Gtk.Button(label=_("Preview"), valign=Gtk.Align.CENTER,
                                          visible=False,
                                          tooltip_text=_("The title block before and after"))
            self.preview_btn.add_css_class("flat")
            self.preview_btn.connect("clicked", lambda _b: PreviewWindow(self._win, self).present())
            self.open_btn = Gtk.Button(icon_name="folder-open-symbolic",
                                       valign=Gtk.Align.CENTER, visible=False,
                                       tooltip_text=_("Open folder"))
            self.open_btn.add_css_class("flat")
            self.open_btn.connect("clicked", lambda _b: self._win.reveal(self.dest))
            for w in (self.stack, texts, self.dates_btn, self.preview_btn, self.open_btn):
                box.append(w)
            outer.append(box)
            self.details = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
            outer.append(self.details)
            self.set_child(outer)

        # --- analysis ---------------------------------------------------
        def start(self, label):
            self.status.set_label(label)
            self.bar.set_fraction(0.0)
            self.bar.set_visible(True)
            self.spinner.start()
            self.stack.set_visible_child_name("spinner")

        def progress(self, fraction, label):
            self.bar.set_fraction(fraction)
            self.status.set_label(f"{label} {int(fraction * 100)}%")

        def _stop(self, icon_name, cls=None):
            self.spinner.stop()
            self.bar.set_visible(False)
            self.icon.set_from_icon_name(icon_name)
            for c in ("dim-label", "error", "warning", "success"):
                self.icon.remove_css_class(c)
            if cls:
                self.icon.add_css_class(cls)
            self.stack.set_visible_child_name("icon")

        def analysed(self, analysis):
            self.analysis = analysis
            self.selection = analysis.default_selection()
            self._build_details()
            usable = any(d.page == 0 for d in analysis.dates) or analysis.later_pages
            self.dates_btn.set_visible(bool(usable))
            self.preview_btn.set_visible(bool(analysis.dates))
            self._refresh()

        def _refresh(self):
            """Status line and icon after the analysis or a change of selection."""
            if self.state in ("done", "error", "busy"):
                return
            self.status.set_label(describe(self.analysis, self.selection))
            if self.selection:
                self.state = "ready"
                self._stop("document-edit-symbolic")
            else:
                self.state = "empty"
                self._stop("action-unavailable-symbolic", "dim-label")
            self._win.update_actions()

        def _build_details(self):
            a = self.analysis
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            set_margins(box, top=0, bottom=8, start=42, end=10)
            for d in (d for d in a.dates if d.page == 0):
                where = _("title block") if d.in_block else _("elsewhere on the page")
                check = Gtk.CheckButton(label=f"{d.index}.  {d.text}  ·  {where}",
                                        active=d.index in self.selection)
                check.connect("toggled", self._on_date_toggled, d.index)
                box.append(check)
            if a.later_pages:
                later = a.later_dates
                check = Gtk.CheckButton(
                    label=_("Also the title block of the other {n} pages ({dates})").format(
                        n=a.later_pages, dates=_date_list(later)),
                    active=a.all_pages)
                check.set_tooltip_text(
                    _("For sets of sheets that repeat the title block on every page, "
                      "like single-line diagrams"))
                check.connect("toggled", self._on_pages_toggled)
                box.append(check)
            hint = Gtk.Label(label=_("The numbers match the outlines in the preview."),
                             xalign=0.0)
            hint.add_css_class("dim-label")
            hint.add_css_class("caption")
            hint.set_margin_top(4)
            box.append(hint)
            self.details.set_child(box)

        def _on_date_toggled(self, check, index):
            if check.get_active():
                self.selection.add(index)
            else:
                self.selection.discard(index)
            self._refresh()

        def _on_pages_toggled(self, check):
            a = self.analysis
            first = {i for i in self.selection
                     if any(d.index == i and d.page == 0 for d in a.dates)}
            a.all_pages = check.get_active()
            later = {i for i in a.default_selection()
                     if any(d.index == i and d.page > 0 for d in a.dates)}
            self.selection = first | later
            self._refresh()

        def failed(self, text, skipped=False):
            self.state = "error" if not skipped else "empty"
            if skipped:
                self._stop("action-unavailable-symbolic", "dim-label")
            else:
                self._stop("dialog-error-symbolic", "error")
            self.status.set_label(text)
            self.dates_btn.set_visible(False)
            self.details.set_reveal_child(False)
            self._win.update_actions()

        # --- writing ----------------------------------------------------
        def written(self, result):
            self.state = "done"
            self._stop("object-select-symbolic", "success")
            self.dest = Path(result.dest).absolute()
            # the folder that was created, not the sub-folder the file is in
            depth = len(self.src.relative_to(self.root).parts) if self.root else 1
            folder = self.dest.parents[depth - 1].name
            self.status.set_label(_("Changed: {n} dates → {folder}").format(
                n=result.changed, folder=folder))
            self.set_tooltip_text(str(self.dest))
            self.set_activatable(True)  # double-click / Enter opens the document
            self.open_btn.set_visible(True)
            self.dates_btn.set_active(False)
            self.dates_btn.set_visible(False)
            self._win.update_actions()

    # --- preview window ---------------------------------------------------

    class PreviewWindow(Gtk.Window):
        """The title block before and after, rendered off-screen with the
        current date and selection. Nothing is written."""

        def __init__(self, parent, row):
            super().__init__(transient_for=parent, modal=False,
                             title=_("Preview — {name}").format(name=row.src.name),
                             default_width=960, default_height=720)
            init_dialog(self)
            self._parent, self._row = parent, row
            a = row.analysis
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            set_margins(box, 16)
            top = Gtk.Box(spacing=8)
            self.caption = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
            self.caption.add_css_class("dim-label")
            top.append(self.caption)
            # pages with something to show: the first, then those changed
            self._pages = [0] + sorted({d.page for d in a.dates
                                        if d.page > 0 and d.index in row.selection})
            if len(self._pages) > 1:
                top.append(Gtk.Label(label=_("Page")))
                self.page_drop = Gtk.DropDown.new_from_strings(
                    [str(p + 1) for p in self._pages])
                self.page_drop.connect("notify::selected", lambda *_a: self._load())
                top.append(self.page_drop)
            else:
                self.page_drop = None
            box.append(top)
            self.spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER,
                                       valign=Gtk.Align.CENTER, vexpand=True)
            self.pictures = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.before = self._picture()
            self.after = self._picture()
            for label, pic in ((_("Before"), self.before), (_("After"), self.after)):
                title = Gtk.Label(label=label, xalign=0.0)
                title.add_css_class("heading")
                self.pictures.append(title)
                frame = Gtk.Frame(child=pic, vexpand=True)
                frame.add_css_class("preview-frame")
                self.pictures.append(frame)
            self.stack = Gtk.Stack(vexpand=True)
            self.stack.add_named(self.spinner, "wait")
            self.stack.add_named(self.pictures, "show")
            box.append(self.stack)
            self.set_child(box)
            self._token = 0
            self._load()

        @staticmethod
        def _picture():
            pic = Gtk.Picture(can_shrink=True, vexpand=True)
            try:
                pic.set_content_fit(Gtk.ContentFit.CONTAIN)  # GTK >= 4.8
            except AttributeError:
                pic.set_keep_aspect_ratio(True)
            return pic

        def _load(self):
            page = self._pages[self.page_drop.get_selected()] if self.page_drop else 0
            new_date = self._parent.new_date() or date.today()
            self.caption.set_label(
                _("New date: {date}. The dates to change are outlined in red; the "
                  "outline appears only here.").format(date=format_user_date(new_date)))
            self.stack.set_visible_child_name("wait")
            self._token += 1
            token = self._token
            self._parent.request_preview(
                self._row.analysis, set(self._row.selection), new_date, page,
                lambda result, err: self._shown(token, result, err))

        def _shown(self, token, result, err):
            if token != self._token:
                return  # a newer page was asked for meanwhile
            if err:
                self.caption.set_label(_("Preview failed: {error}").format(error=err))
                self.stack.set_visible_child_name("show")
                return
            before, after = result
            self.before.set_paintable(texture(before))
            self.after.set_paintable(texture(after) if after else None)
            self.stack.set_visible_child_name("show")

    # --- main window ------------------------------------------------------

    # The system title bar needs a plain GTK window: libadwaita's always
    # draws its own. Its content is libadwaita's all the same.
    WindowBase = Adw.ApplicationWindow if use_adw and use_csd else Gtk.ApplicationWindow

    class Window(WindowBase):
        def __init__(self, app):
            super().__init__(
                application=app, title=APP_NAME,
                default_width=760, default_height=620,
            )
            self.settings = app.settings
            self._overwrite = False
            self._jobs: queue.PriorityQueue = queue.PriorityQueue()
            self._seq = itertools.count()
            self._rows = {}           # key -> FileRow
            self._busy = 0            # jobs queued or running
            self._native = None       # keep FileChooserNative alive
            self._banner_cb = None
            self._toast = None        # the last one (libadwaita)
            self._about = None
            self.connect("map", lambda _w: _mark("window-mapped"))

            header = Adw.HeaderBar() if use_adw else Gtk.HeaderBar()
            if not use_csd:  # native title bar: the header bar becomes a plain toolbar
                if use_adw:
                    header.set_show_start_title_buttons(False)
                    header.set_show_end_title_buttons(False)
                else:
                    header.set_show_title_buttons(False)
                header.set_title_widget(Gtk.Box())
            elif not use_adw:
                self.set_titlebar(header)    # libadwaita's: placed by the AdwToolbarView below

            menu = Gio.Menu()
            if is_win:
                section = Gio.Menu()
                section.append(_("Check for updates…"), "app.check-updates")
                menu.append_section(None, section)
            section = Gio.Menu()
            section.append(_("Preferences"), "app.preferences")
            menu.append_section(None, section)
            section = Gio.Menu()
            section.append(_("About {app}").format(app=APP_NAME), "app.about")
            menu.append_section(None, section)
            self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                           primary=True, tooltip_text=_("Main menu (F10)"))
            header.pack_end(self.menu_btn)
            self.spinner = Gtk.Spinner(tooltip_text=_("Working…"))
            header.pack_end(self.spinner)
            self.clear_btn = Gtk.Button(icon_name="edit-clear-all-symbolic",
                                        tooltip_text=_("Clear the list"), sensitive=False)
            self.clear_btn.connect("clicked", self.on_clear)
            header.pack_start(self.clear_btn)
            if is_win:  # Windows habit: a tap on Alt opens the main menu (F10 in GTK)
                self._alt_solo = False
                for widget in (self, self.menu_btn.get_popover()):
                    keys = Gtk.EventControllerKey()
                    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
                    keys.connect("key-pressed", self._on_key_pressed)
                    keys.connect("key-released", self._on_key_released)
                    widget.add_controller(keys)
                self.connect("notify::is-active",
                             lambda _w, _p: setattr(self, "_alt_solo", False))

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            if use_adw:
                self.toasts = Adw.ToastOverlay(child=content)
                view = Adw.ToolbarView()
                view.add_top_bar(header)
                view.set_content(self.toasts)
                if use_csd:
                    self.set_content(view)
                else:
                    self.set_child(view)
            else:
                set_margins(content, 16)
                root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                if not use_csd:
                    root.append(header)
                root.append(content)
                self.set_child(root)
                # --- in-app notification banner -----------------------------
                self.banner = Gtk.Revealer(
                    transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
                bbox = Gtk.Box(spacing=12)
                bbox.add_css_class("app-banner")
                self.banner_label = Gtk.Label(hexpand=True, xalign=0.0, wrap=True)
                self.banner_button = Gtk.Button(valign=Gtk.Align.CENTER)
                self.banner_button.connect("clicked", self._on_banner_button)
                close_btn = Gtk.Button(icon_name="window-close-symbolic",
                                       valign=Gtk.Align.CENTER,
                                       tooltip_text=_("Close"))
                close_btn.add_css_class("flat")
                close_btn.connect(
                    "clicked", lambda _b: self.banner.set_reveal_child(False))
                for w in (self.banner_label, self.banner_button, close_btn):
                    bbox.append(w)
                self.banner.set_child(bbox)
                content.append(self.banner)
            theme.watch_window(self)

            # --- drop zone -------------------------------------------------
            btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                           halign=Gtk.Align.CENTER)
            b_files = Gtk.Button(label=_("Choose files…"), action_name="app.open-files")
            b_folder = Gtk.Button(label=_("Choose folder…"), action_name="app.open-folder")
            btns.append(b_files)
            btns.append(b_folder)
            title_text = _("Drop PDF files or folders here")
            hint_text = _("The changed copies go to a new folder; the originals are not touched")
            if use_adw:
                self.dropzone = Adw.StatusPage(
                    icon_name="document-open-symbolic", title=title_text,
                    description=hint_text, child=btns, vexpand=False)
                self.dropzone.add_css_class("compact")
            else:
                self.dropzone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                        spacing=8)
                icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
                icon.set_pixel_size(48)
                icon.set_margin_top(20)
                title = Gtk.Label(label=title_text)
                title.add_css_class("title-4")
                hint = Gtk.Label(label=hint_text, wrap=True,
                                 justify=Gtk.Justification.CENTER)
                hint.add_css_class("dim-label")
                btns.set_margin_bottom(20)
                for w in (icon, title, hint, btns):
                    self.dropzone.append(w)
            self.dropzone.add_css_class("dropzone")
            if use_adw:
                set_margins(self.dropzone, top=20, bottom=0, start=16, end=16)
            else:
                set_margins(self.dropzone, top=4, bottom=4)
            content.append(self.dropzone)

            # --- files list ------------------------------------------------
            self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.listbox.connect("row-activated", self._on_row_activated)
            placeholder = Gtk.Label(label=_("The PDF files and the dates found will appear here"),
                                    wrap=True)
            placeholder.add_css_class("dim-label")
            scrolled = Gtk.ScrolledWindow(vexpand=True, child=self.listbox)
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            if use_adw:
                self.listbox.add_css_class("boxed-list")
                self.listbox.set_valign(Gtk.Align.START)
                set_margins(self.listbox, top=4, bottom=12, start=8, end=8)
                set_margins(scrolled, start=8, end=8)
                placeholder.set_vexpand(True)
                set_margins(placeholder, start=16, end=16)
                self.list_stack = Gtk.Stack(
                    vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
                self.list_stack.add_named(placeholder, "empty")
                self.list_stack.add_named(scrolled, "list")
                content.append(self.list_stack)
            else:
                set_margins(placeholder, top=24, bottom=24)
                self.listbox.set_placeholder(placeholder)
                content.append(Gtk.Frame(child=scrolled))

            # --- bottom bar: new date, options, action -----------------------
            bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            if use_adw:
                set_margins(bottom, bottom=16, start=16, end=16)
            date_label = Gtk.Label(label=_("New date"))
            self.date_entry = Gtk.Entry(text=format_user_date(date.today()), width_chars=11,
                                        max_width_chars=11, valign=Gtk.Align.CENTER,
                                        tooltip_text=_("dd/mm/yyyy"))
            self.date_entry.connect("changed", lambda _e: self.update_actions())
            self.calendar = Gtk.Calendar()
            self.calendar.connect("day-selected", self._on_day_selected)
            pop = Gtk.Popover(child=self.calendar)
            pop.connect("show", lambda _p: self._sync_calendar())
            cal_btn = Gtk.MenuButton(icon_name="x-office-calendar-symbolic", popover=pop,
                                     valign=Gtk.Align.CENTER, tooltip_text=_("Calendar"))
            self.date_box = Gtk.Box(spacing=0)
            self.date_box.add_css_class("linked")
            self.date_box.append(self.date_entry)
            self.date_box.append(cal_btn)
            check = Gtk.CheckButton(label=_("Overwrite existing files"))
            check.set_margin_start(8)
            check.connect("toggled", lambda c: setattr(self, "_overwrite", c.get_active()))
            self.summary = Gtk.Label(label="", hexpand=True, xalign=1.0)
            self.summary.set_ellipsize(Pango.EllipsizeMode.START)
            self.summary.add_css_class("dim-label")
            self.apply_btn = Gtk.Button(label=_("Change dates"), sensitive=False)
            self.apply_btn.add_css_class("suggested-action")
            self.apply_btn.connect("clicked", self.on_apply)
            for w in (date_label, self.date_box, check, self.summary, self.apply_btn):
                bottom.append(w)
            content.append(bottom)

            # --- drag & drop ----------------------------------------------
            drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
            drop.connect("drop", self.on_drop)
            drop.connect("enter", self.on_drop_enter)
            # "motion" must keep returning COPY on every pointer move:
            # without it Windows gets DROPEFFECT_NONE mid-drag and hides
            # the drag cursor/icon while hovering the window.
            drop.connect("motion", self.on_drop_motion)
            drop.connect("leave", self.on_drop_leave)
            self.add_controller(drop)

            threading.Thread(target=self._worker, daemon=True).start()

        # --- public API used by the application ------------------------------
        def enqueue(self, paths):
            """Analyse new files (the folder scan runs on the worker thread)."""
            paths = [p for p in paths if p]
            if not paths:
                return
            self._put(1, ("scan", paths))

        def new_date(self):
            """The date in the entry, or None when it is not a valid date."""
            try:
                return parse_user_date(self.date_entry.get_text())
            except ValueError:
                return None

        def request_preview(self, analysis, selection, new_date, page, callback):
            # priority 0: ahead of a long batch of analyses or writes
            self._put(0, ("preview", analysis, selection, new_date, page, callback))

        def update_actions(self):
            valid = self.new_date() is not None
            if valid:
                self.date_entry.remove_css_class("error")
            else:
                self.date_entry.add_css_class("error")
            ready = [r for r in self._rows.values() if r.state == "ready"]
            self.apply_btn.set_sensitive(valid and bool(ready))
            self.clear_btn.set_sensitive(bool(self._rows) and self._busy == 0)
            if self._busy == 0:
                self._set_counts()

        def first_shown(self):
            """One-shot tasks after the window is on screen (Windows only:
            the daily update check)."""
            if is_win:
                threading.Thread(target=clean_update_downloads, daemon=True).start()
                today = date.today().isoformat()
                if (self.settings.get_bool("check_updates")
                        and self.settings.get("last_update_check") != today):
                    self.settings.set("last_update_check", today)
                    self.check_updates(manual=False)
            return False  # one-shot GLib.idle_add

        def show_banner(self, text, button_label=None, callback=None):
            self._banner_cb = callback
            if use_adw:
                toast = self._toast = Adw.Toast(title=text, use_markup=False)
                if button_label:
                    toast.set_button_label(button_label)
                    toast.set_action_name("app.banner-action")
                    toast.set_timeout(0)
                self.toasts.add_toast(toast)
                return
            self.banner_label.set_label(text)
            self.banner_button.set_label(button_label or "")
            self.banner_button.set_visible(bool(button_label))
            self.banner.set_reveal_child(True)

        def run_banner_action(self):
            if self._banner_cb:
                self._banner_cb()

        def hide_banner(self):
            if use_adw:
                if self._toast is not None:
                    self._toast.dismiss()
            else:
                self.banner.set_reveal_child(False)

        def _on_banner_button(self, _btn):
            self.banner.set_reveal_child(False)
            self.run_banner_action()

        def show_about(self):
            comments = _("Changes the dates in the title block of PDF drawings and "
                         "documents, many files at once.")
            if use_adw:  # AdwAboutDialog: the current GNOME about window
                about = Adw.AboutDialog(
                    application_name=APP_NAME, application_icon=APP_ID,
                    version=__version__, comments=comments,
                    website=f"https://github.com/{GITHUB_REPO}",
                    issue_url=f"https://github.com/{GITHUB_REPO}/issues",
                    license_type=Gtk.License.AGPL_3_0,
                    copyright="© 2026 Daniel Grasso",
                    developer_name="Daniel Grasso",
                    developers=["Daniel Grasso"],
                )
                about.add_legal_section("MuPDF, PyMuPDF", "© Artifex Software, Inc.",
                                        Gtk.License.AGPL_3_0, None)
                about.add_legal_section("fontTools", None, Gtk.License.MIT_X11, None)
                about.present(self)
                return
            if self._about is None:  # built once, hidden on close
                self._about = Gtk.AboutDialog(
                    transient_for=self, modal=True, hide_on_close=True,
                    program_name=APP_NAME, version=__version__, comments=comments,
                    website=f"https://github.com/{GITHUB_REPO}",
                    website_label=_("Project on GitHub"),
                    license_type=Gtk.License.AGPL_3_0,
                    copyright="© 2026 Daniel Grasso",
                    authors=["Daniel Grasso"],
                    logo_icon_name=APP_ID,
                )
                self._about.add_credit_section(_("PDF engine"), ["MuPDF (PyMuPDF)"])
            self._about.present()

        # --- update check (Windows) -----------------------------------------
        def check_updates(self, manual):
            if manual:
                self.show_banner(_("Checking for updates…"))

            def work():
                try:
                    rel = fetch_latest_release()
                except UpdateError as e:
                    GLib.idle_add(self._update_result, None, str(e), manual)
                    return
                GLib.idle_add(self._update_result, rel, None, manual)
            threading.Thread(target=work, daemon=True).start()

        def _update_result(self, rel, err, manual):
            if err:
                if manual:
                    self.show_banner(_("Update check failed: {error}").format(error=err))
                return False
            if is_newer(rel["version"]):
                if manual:
                    self.hide_banner()
                    UpdateDialog(self, rel).open()
                else:
                    self.show_banner(
                        _("Version {version} of {app} is available.").format(
                            version=rel["version"], app=APP_NAME),
                        _("Update…"), lambda: UpdateDialog(self, rel).open())
            elif manual:
                self.show_banner(_("{app} {version} is up to date: no newer version.").format(
                    app=APP_NAME, version=__version__))
            return False

        # --- signal handlers ----------------------------------------------
        def _on_key_pressed(self, _ctl, keyval, _code, _state):
            # remember whether Alt is being pressed on its own
            self._alt_solo = keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R)
            return False

        def _on_key_released(self, _ctl, keyval, _code, _state):
            if keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R) and self._alt_solo:
                self._alt_solo = False
                if self.menu_btn.get_active():
                    self.menu_btn.popdown()
                else:
                    self.menu_btn.popup()

        def _sync_calendar(self):
            value = self.new_date()
            if value is None:
                return
            try:
                self.calendar.select_day(GLib.DateTime.new_local(
                    value.year, value.month, value.day, 0, 0, 0))
            except (TypeError, AttributeError):
                pass

        def _on_day_selected(self, calendar):
            dt = calendar.get_date()
            value = date(dt.get_year(), dt.get_month(), dt.get_day_of_month())
            if value != self.new_date():
                self.date_entry.set_text(format_user_date(value))

        def on_clear(self, _btn):
            if self._busy:
                return
            for row in list(self._rows.values()):
                self.listbox.remove(row)
            self._rows.clear()
            if use_adw:
                self.list_stack.set_visible_child_name("empty")
            self.summary.set_label("")
            self.update_actions()

        def on_apply(self, _btn):
            new_date = self.new_date()
            if new_date is None:
                return
            rows = [(k, r) for k, r in self._rows.items() if r.state == "ready"]
            for key, row in rows:
                row.state = "busy"
                dest = output_path(row.src, row.root, new_date)
                row.start(_("Changing…"))
                self._put(1, ("write", key, row.analysis, set(row.selection), dest,
                              new_date, self._overwrite))
            self.update_actions()

        def _on_row_activated(self, _listbox, row):
            """Open the changed document with its default application."""
            dest = getattr(row, "dest", None)
            if dest is None:
                return
            try:
                if is_win:
                    os.startfile(str(dest))
                elif has_filedialog:  # GTK >= 4.10
                    Gtk.FileLauncher.new(Gio.File.new_for_path(str(dest))).launch(
                        self, None, None)
                else:
                    Gio.AppInfo.launch_default_for_uri(dest.as_uri(), None)
            except (OSError, GLib.Error) as e:
                self.show_banner(_("Cannot open {name}: {error}").format(
                    name=dest.name, error=e))

        def on_drop_enter(self, _target, _x, _y):
            self.dropzone.add_css_class("hover")
            return Gdk.DragAction.COPY

        def on_drop_motion(self, _target, _x, _y):
            return Gdk.DragAction.COPY

        def on_drop_leave(self, _target):
            self.dropzone.remove_css_class("hover")

        def on_drop(self, _target, value, _x, _y):
            self.dropzone.remove_css_class("hover")
            self.enqueue([f.get_path() for f in value.get_files()])
            return True

        def on_pick_files(self):
            if has_filedialog:
                dlg = Gtk.FileDialog(title=_("Choose PDF files"))
                f_pdf = Gtk.FileFilter()
                f_pdf.set_name(_("PDF documents (*.pdf)"))
                f_pdf.add_pattern("*.pdf")
                f_pdf.add_pattern("*.PDF")
                f_all = Gtk.FileFilter()
                f_all.set_name(_("All files"))
                f_all.add_pattern("*")
                store = Gio.ListStore.new(Gtk.FileFilter)
                store.append(f_pdf)
                store.append(f_all)
                dlg.set_filters(store)
                dlg.set_default_filter(f_pdf)
                dlg.open_multiple(self, None, self._files_chosen)
            else:
                self._native = Gtk.FileChooserNative.new(
                    _("Choose PDF files"), self, Gtk.FileChooserAction.OPEN,
                    _("Open"), _("Cancel"))
                self._native.set_select_multiple(True)
                self._native.connect("response", self._native_response)
                self._native.show()

        def on_pick_folder(self):
            if has_filedialog:
                dlg = Gtk.FileDialog(title=_("Choose a folder"))
                dlg.select_folder(self, None, self._folder_chosen)
            else:
                self._native = Gtk.FileChooserNative.new(
                    _("Choose a folder"), self,
                    Gtk.FileChooserAction.SELECT_FOLDER, _("Open"), _("Cancel"))
                self._native.connect("response", self._native_response)
                self._native.show()

        def _files_chosen(self, dlg, res):
            try:
                files = dlg.open_multiple_finish(res)
            except GLib.Error:
                return
            self.enqueue([files.get_item(i).get_path()
                          for i in range(files.get_n_items())])

        def _folder_chosen(self, dlg, res):
            try:
                folder = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            if folder:
                self.enqueue([folder.get_path()])

        def _native_response(self, native, response):
            if response == Gtk.ResponseType.ACCEPT:
                files = native.get_files()
                self.enqueue([files.get_item(i).get_path()
                              for i in range(files.get_n_items())])
            self._native = None

        # --- worker thread ------------------------------------------------
        def _put(self, priority, job):
            self._busy += 1
            self.spinner.start()
            self.clear_btn.set_sensitive(False)
            self._jobs.put((priority, next(self._seq), job))

        def _worker(self):
            # The only thread that talks to MuPDF, which is not thread-safe.
            while True:
                _prio, _n, job = self._jobs.get()
                kind = job[0]
                try:
                    if kind == "scan":
                        self._do_scan(job[1])
                    elif kind == "analyse":
                        self._do_analyse(*job[1:])
                    elif kind == "write":
                        self._do_write(*job[1:])
                    elif kind == "preview":
                        self._do_preview(*job[1:])
                finally:
                    GLib.idle_add(self._job_done)

        def _do_scan(self, paths):
            files = iter_pdf(host_paths(paths))
            if not files:
                GLib.idle_add(self.show_banner, _("No PDF files found."))
            for src, root in files:
                key = next(self._seq)
                GLib.idle_add(self._row_add, key, src, root)
                GLib.idle_add(self._put, 1, ("analyse", key, src))

        def _do_analyse(self, key, src):
            GLib.idle_add(self._row_call, key, "start", _("Looking for dates…"))
            try:
                analysis = analyse_file(src, progress=self._progress_reporter(
                    key, _("Looking for dates…")))
            except (CartiglioError, OSError) as e:
                GLib.idle_add(self._row_call, key, "failed", _("Error: {error}").format(error=e))
                return
            except Exception as e:  # a page MuPDF chokes on must not stop the queue
                GLib.idle_add(self._row_call, key, "failed",
                              _("Error: {error}").format(error=f"{type(e).__name__}: {e}"))
                return
            GLib.idle_add(self._row_call, key, "analysed", analysis)

        def _do_write(self, key, analysis, selection, dest, new_date, overwrite):
            try:
                result = redate_file(analysis, dest, new_date, selection, overwrite,
                                     progress=self._progress_reporter(key, _("Changing…")))
            except FileExistsError:
                GLib.idle_add(self._row_call, key, "failed",
                              _("Skipped: {name} already exists in the output folder "
                                "(enable Overwrite)").format(name=Path(dest).name), True)
                return
            except NothingToChange:
                GLib.idle_add(self._row_call, key, "failed", _("Skipped: no date selected"), True)
                return
            except (CartiglioError, OSError) as e:
                GLib.idle_add(self._row_call, key, "failed", _("Error: {error}").format(error=e))
                return
            except Exception as e:
                GLib.idle_add(self._row_call, key, "failed",
                              _("Error: {error}").format(error=f"{type(e).__name__}: {e}"))
                return
            GLib.idle_add(self._row_call, key, "written", result)

        def _do_preview(self, analysis, selection, new_date, page, callback):
            try:
                result = preview_png(analysis, new_date, selection, width_px=1400, page=page)
                GLib.idle_add(callback, result, None)
            except Exception as e:
                GLib.idle_add(callback, None, str(e))

        def _progress_reporter(self, key, label):
            """progress(fraction) callback, throttled so the main loop is not
            flooded on documents with many pages."""
            last = [-1.0, 0.0]  # fraction, monotonic time

            def report(fraction):
                now = time.monotonic()
                if fraction - last[0] >= 0.02 and now - last[1] >= 0.05:
                    last[0], last[1] = fraction, now
                    GLib.idle_add(self._row_call, key, "progress", fraction, label)
            return report

        # --- UI updates (main thread) -------------------------------------
        def _job_done(self):
            self._busy = max(0, self._busy - 1)
            if self._busy == 0:
                self.spinner.stop()
            self.update_actions()
            return False

        def _row_add(self, key, src, root):
            row = FileRow(src, root, self)
            self._rows[key] = row
            self.listbox.append(row)
            if use_adw:  # first row: swap the empty state for the card
                self.list_stack.set_visible_child_name("list")
            return False  # one-shot GLib.idle_add

        def _row_call(self, key, method, *args):
            row = self._rows.get(key)
            if row is not None:
                getattr(row, method)(*args)
            return False

        def _set_counts(self):
            states = Counter(r.state for r in self._rows.values())
            if not self._rows:
                self.summary.set_label("")
                return
            parts = []
            if states["ready"]:
                parts.append(_("{n} to change").format(n=states["ready"]))
            if states["done"]:
                parts.append(_("{n} changed").format(n=states["done"]))
            if states["empty"]:
                parts.append(_("{n} without dates").format(n=states["empty"]))
            if states["error"]:
                parts.append(_("{n} errors").format(n=states["error"]))
            self.summary.set_label(" · ".join(parts))

        def reveal(self, dest):
            """Show the changed file in the platform file manager.

            Gio.AppInfo.launch_default_for_uri silently fails for file://
            URIs on Windows, hence the per-platform paths.
            """
            if dest is None:
                return
            if is_win:
                # Explorer wants exactly  /select,"<path>" : passing a list
                # lets Popen quote the whole switch, which Explorer ignores
                # (it then opens Documents). Drive letters are kept as they
                # are (no resolve()), so mapped network drives stay mapped.
                try:
                    subprocess.Popen(f'explorer /select,"{dest}"')
                except OSError:
                    os.startfile(dest.parent)
            elif has_filedialog:  # GTK >= 4.10
                launcher = Gtk.FileLauncher.new(
                    Gio.File.new_for_path(str(dest)))
                launcher.open_containing_folder(self, None, None)
            else:
                Gio.AppInfo.launch_default_for_uri(
                    dest.parent.as_uri(), None)

    # The switch to the system title bar, in either kind of preferences
    TITLE_BAR_HINT = _("Needed to snap the window to the screen edges "
                       "(takes effect at the next start)")

    # --- File Explorer entry (Windows): what each action tells the user -----

    def shell_text():
        try:
            registered = win_is_registered()
        except OSError:
            registered = False
        if registered:
            return _("Currently: present (on Windows 11 under “Show more options”)")
        return _("Currently: not present")

    def register():
        try:
            win_register()
            return _("Context-menu entry added.")
        except OSError as e:
            return _("Registration failed: {error}").format(error=e)

    def unregister():
        try:
            win_unregister()
            return _("Context-menu entry removed for the current user.")
        except OSError as e:
            return _("Removal failed: {error}").format(error=e)

    # --- update dialog (Windows) --------------------------------------------

    class UpdateDialog(Adw.Dialog if use_adw else Gtk.Window):
        def __init__(self, parent, rel):
            if use_adw:
                super().__init__(title=_("Update available"), content_width=480)
                self.connect("closed", self._on_close)
            else:
                super().__init__(transient_for=parent, modal=True, resizable=False,
                                 title=_("Update available"), default_width=480)
                init_dialog(self)
                self.connect("close-request", self._on_close)
            self._parent = parent
            self._app = parent.get_application()
            self._rel = rel
            self._cancel = threading.Event()

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            set_margins(box, 24)
            heading = Gtk.Label(label=_("{app} {version} is available").format(
                app=APP_NAME, version=rel["version"]), xalign=0.0, wrap=True)
            heading.add_css_class("title-2")
            sub = Gtk.Label(label=_("You are using version {version}.").format(
                version=__version__), xalign=0.0)
            sub.add_css_class("dim-label")
            box.append(heading)
            box.append(sub)
            notes = rel["notes"].strip()
            if notes:
                lbl = Gtk.Label(label=notes[:4000], xalign=0.0, yalign=0.0,
                                wrap=True, selectable=True, max_width_chars=56)
                set_margins(lbl, 8)
                sc = Gtk.ScrolledWindow(child=lbl, min_content_height=120,
                                        max_content_height=240,
                                        propagate_natural_height=True)
                sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                box.append(Gtk.Frame(child=sc))
            self.progress = Gtk.ProgressBar(show_text=True, visible=False)
            self.status = Gtk.Label(xalign=0.0, wrap=True)
            self.status.add_css_class("dim-label")
            box.append(self.progress)
            box.append(self.status)

            later = Gtk.Button(label=_("Later"))
            later.connect("clicked", lambda _b: self.close())
            self.asset = pick_asset(rel["assets"], is_installed_build()) if is_win else None
            if self.asset and is_installed_build():
                self.action = Gtk.Button(label=_("Download and install"))
                self.action.connect("clicked", self._download)
                self.status.set_label(_("The installation replaces the current "
                                        "version; the app will close."))
            else:
                self.action = Gtk.Button(label=_("Open the download page"))
                self.action.connect("clicked", self._open_page)
            self.action.add_css_class("suggested-action")
            box.append(button_row(later, self.action))
            if use_adw:
                box.set_margin_top(6)       # right under the header bar
                view = Adw.ToolbarView(content=box)
                view.add_top_bar(Adw.HeaderBar())
                self.set_child(view)
            else:
                self.set_child(box)
            self.set_default_widget(self.action)
            # the notes are selectable: the dialog would open with them
            # focused and all their text selected
            self.set_focus(self.action)

        def open(self):
            if use_adw:
                self.present(self._parent)
            else:
                self.present()

        def _on_close(self, _win):
            self._cancel.set()
            return False

        def _open_page(self, _btn):
            # the launcher wants a GtkWindow: an AdwDialog is not one
            open_uri(self._rel["url"], self.get_root())
            self.close()

        def _download(self, _btn):
            self.action.set_sensitive(False)
            self.progress.set_visible(True)
            self.progress.set_fraction(0)
            self.status.set_label(_("Downloading…"))

            def progress(done, total):
                GLib.idle_add(self._on_progress, done, total)

            def work():
                try:
                    dest = update_download_folder() / self.asset["name"]
                    download_file(self.asset["url"], dest, progress, self._cancel,
                                  sha256=self.asset.get("sha256", ""), size=self.asset.get("size", 0))
                except Exception as e:  # whatever it is, said: never stuck on "Downloading…"
                    GLib.idle_add(self._on_error, str(e))
                    return
                GLib.idle_add(self._on_downloaded, dest)
            threading.Thread(target=work, daemon=True).start()

        def _on_progress(self, done, total):
            if total:
                self.progress.set_fraction(min(done / total, 1.0))
                self.progress.set_text(f"{done / 1e6:.1f} / {total / 1e6:.1f} MB")
            else:
                self.progress.pulse()
            return False

        def _on_error(self, msg):
            self.progress.set_visible(False)
            self.status.set_label(_("Download failed: {error}").format(error=msg))
            self.action.set_sensitive(True)
            return False

        def _on_downloaded(self, dest):
            self.status.set_label(_("Starting the installer…"))
            try:
                os.startfile(str(dest))
            except OSError as e:
                self.status.set_label(_("Cannot start the installer: {error}").format(error=e))
                self.action.set_sensitive(True)
                return False
            self._app.quit()  # let the installer replace the files in peace
            return False

    # --- preferences --------------------------------------------------------

    class PreferencesWindow(Gtk.Window):
        def __init__(self, parent):
            super().__init__(transient_for=parent, modal=True, resizable=False,
                             title=_("Preferences"), default_width=540)
            init_dialog(self)
            self._parent = parent
            self.shell_label = None
            self._schemes = (("auto", _("Automatic (follow the system)")),
                             ("light", _("Light")), ("dark", _("Dark")))
            self._languages = (("auto", _("Automatic (system language)")),
                               ("it", "Italiano"), ("en", "English"))
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            set_margins(box, 24)

            box.append(self._section(_("Appearance")))
            look = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            look.append(self._row(
                _("Theme"), _("“Automatic” follows the system settings"),
                self._dropdown(self._schemes, "color_scheme", self._on_scheme)))
            look.append(self._row(
                _("Language"), _("Takes effect at the next start"),
                self._dropdown(self._languages, "language", self._on_language)))
            if is_win:
                look.append(self._row(
                    _("Windows title bar"), TITLE_BAR_HINT, self._switch("system_title_bar")))
            box.append(Gtk.Frame(child=look))

            if is_win:
                box.append(self._section(_("General")))
                general = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                general.append(self._row(
                    _("Check for updates at start-up"),
                    _("Once a day, from the project's GitHub Releases"),
                    self._switch("check_updates")))
                box.append(Gtk.Frame(child=general))

                box.append(self._section(_("File Explorer")))
                shell = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                reg = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
                b_reg = Gtk.Button(label=_("Add"))
                b_reg.connect("clicked", self._on_register)
                b_unreg = Gtk.Button(label=_("Remove"))
                b_unreg.connect("clicked", self._on_unregister)
                reg.append(b_reg)
                reg.append(b_unreg)
                row, self.shell_label = self._row(
                    _("“Change title block dates” in the context menu of PDF files "
                      "and folders"),
                    shell_text(), reg, return_subtitle=True)
                shell.append(row)
                box.append(Gtk.Frame(child=shell))
            self.set_child(box)

        @staticmethod
        def _dropdown(options, key, callback):
            keys = [k for k, _label in options]
            drop = Gtk.DropDown.new_from_strings([label for _k, label in options])
            drop.set_valign(Gtk.Align.CENTER)
            current = settings.get(key)
            drop.set_selected(keys.index(current) if current in keys else 0)
            drop.connect("notify::selected", callback)
            return drop

        def _on_scheme(self, drop, _pspec):
            settings.set("color_scheme", self._schemes[drop.get_selected()][0])
            theme.apply()

        def _on_language(self, drop, _pspec):
            settings.set("language", self._languages[drop.get_selected()][0])
            self._parent.show_banner(_("The new language will be used at the next start."))

        @staticmethod
        def _section(text):
            lbl = Gtk.Label(label=text, xalign=0.0)
            lbl.add_css_class("heading")
            lbl.set_margin_top(8)
            return lbl

        @staticmethod
        def _switch(key):
            sw = Gtk.Switch(active=settings.get_bool(key), valign=Gtk.Align.CENTER)
            sw.connect("state-set",
                       lambda _s, state: (settings.set(key, bool(state)), False)[1])
            return sw

        @staticmethod
        def _row(title, subtitle, widget, return_subtitle=False):
            row = Gtk.ListBoxRow(activatable=False)
            hbox = Gtk.Box(spacing=12)
            set_margins(hbox, top=8, bottom=8, start=12, end=12)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                            valign=Gtk.Align.CENTER)
            t = Gtk.Label(label=title, xalign=0.0, wrap=True)
            st = Gtk.Label(label=subtitle, xalign=0.0, wrap=True)
            st.add_css_class("dim-label")
            st.add_css_class("caption")
            texts.append(t)
            texts.append(st)
            hbox.append(texts)
            hbox.append(widget)
            row.set_child(hbox)
            return (row, st) if return_subtitle else row

        def _refresh(self, message=None):
            self.shell_label.set_label(shell_text())
            if message:
                self._parent.show_banner(message)

        def _on_register(self, _btn):
            self._refresh(register())

        def _on_unregister(self, _btn):
            self._refresh(unregister())

    def adw_preferences(parent):
        """Preferences as an AdwPreferencesDialog: HIG boxed list, combo rows.
        The plain-GTK PreferencesWindow above serves the windows without
        libadwaita."""
        dialog = Adw.PreferencesDialog(title=_("Preferences"))
        schemes = (("auto", _("Automatic (follow the system)")),
                   ("light", _("Light")), ("dark", _("Dark")))
        languages = (("auto", _("Automatic (system language)")),
                     ("it", "Italiano"), ("en", "English"))

        def on_scheme(value):
            settings.set("color_scheme", value)
            theme.apply()

        def tell(message):      # over the dialog, where the user is looking
            dialog.add_toast(Adw.Toast(title=message, use_markup=False))

        def on_language(value):
            settings.set("language", value)
            tell(_("The new language will be used at the next start."))

        def combo(title, subtitle, options, key, callback):
            keys = [k for k, _label in options]
            row = Adw.ComboRow(
                title=title, subtitle=subtitle, use_markup=False,
                model=Gtk.StringList.new([label for _k, label in options]))
            current = settings.get(key)
            row.set_selected(keys.index(current) if current in keys else 0)
            row.connect("notify::selected",
                        lambda r, _p: callback(keys[r.get_selected()]))
            return row

        def switch(title, subtitle, key):
            row = Adw.SwitchRow(title=title, subtitle=subtitle, use_markup=False,
                                active=settings.get_bool(key))
            row.connect("notify::active", lambda r, _p: settings.set(key, r.get_active()))
            return row

        def action_row(title, subtitle, *buttons):
            row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
            for label, callback in buttons:
                button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
                button.connect("clicked", lambda _b, c=callback: c(row))
                row.add_suffix(button)
            return row

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=_("Appearance"))
        group.add(combo(_("Theme"), _("“Automatic” follows the system settings"),
                        schemes, "color_scheme", on_scheme))
        group.add(combo(_("Language"), _("Takes effect at the next start"),
                        languages, "language", on_language))
        if is_win:
            group.add(switch(_("Windows title bar"), TITLE_BAR_HINT, "system_title_bar"))
        page.add(group)

        if is_win:
            group = Adw.PreferencesGroup(title=_("General"))
            group.add(switch(_("Check for updates at start-up"),
                             _("Once a day, from the project's GitHub Releases"),
                             "check_updates"))
            page.add(group)

            def then_refresh(action):
                def run(row):
                    tell(action())
                    row.set_subtitle(shell_text())
                return run

            group = Adw.PreferencesGroup(title=_("File Explorer"))
            group.add(action_row(
                _("“Change title block dates” in the context menu of PDF files "
                  "and folders"),
                shell_text(),
                (_("Add"), then_refresh(register)),
                (_("Remove"), then_refresh(unregister))))
            page.add(group)

        dialog.add(page)
        dialog.present(parent)

    # --- application --------------------------------------------------------

    AppBase = Adw.Application if use_adw else Gtk.Application

    class App(AppBase):
        """Single-instance application: a second launch (context-menu entry,
        several files selected at once) forwards its command line to the
        running window instead of opening another one."""

        def __init__(self):
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.settings = settings
            self.window = None

        def do_startup(self):
            nonlocal theme
            AppBase.do_startup(self)
            _mark("startup")
            display = Gdk.Display.get_default()
            bundle = getattr(sys, "_MEIPASS", None)
            if bundle:  # icons shipped inside the PyInstaller bundle
                Gtk.IconTheme.get_for_display(display).add_search_path(
                    os.path.join(bundle, "share", "icons"))
            else:  # source checkout: data/icons next to this file (no-op when installed)
                icons = Path(__file__).resolve().parent / "data" / "icons"
                if icons.is_dir():
                    Gtk.IconTheme.get_for_display(display).add_search_path(str(icons))
            Gtk.Window.set_default_icon_name(APP_ID)

            css = Gtk.CssProvider()
            css_data = _CSS_ADW if use_adw else _CSS
            try:  # GTK >= 4.12
                css.load_from_string(css_data.decode())
            except AttributeError:  # older GTK 4, PyGObject signature varies
                try:
                    css.load_from_data(css_data)
                except TypeError:
                    css.load_from_data(css_data, len(css_data))
            Gtk.StyleContext.add_provider_for_display(
                display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            theme = ThemeManager()

            self._action("quit", lambda _a, _p: self.quit(), ["<Control>q"])
            self._action("about", lambda _a, _p: self._win().show_about())
            self._action("open-files", lambda _a, _p: self._win().on_pick_files(),
                         ["<Control>o"])
            self._action("open-folder", lambda _a, _p: self._win().on_pick_folder(),
                         ["<Control><Shift>o"])
            self._action("preferences", lambda _a, _p: (
                adw_preferences(self._win()) if use_adw
                else PreferencesWindow(self._win()).present()),
                ["<Control>comma"])
            self._action("banner-action",
                         lambda _a, _p: self._win().run_banner_action())
            if is_win:
                self._action("check-updates",
                             lambda _a, _p: self._win().check_updates(manual=True))

        def _action(self, name, callback, accels=()):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", list(accels))

        def _win(self):
            if self.window is None:
                self.window = Window(self)
            return self.window

        def do_activate(self):
            self._show(())

        def do_open(self, files, _n_files, _hint):  # D-Bus activation
            self._show([f.get_path() for f in files])

        def do_command_line(self, cmdline):
            # Runs in the primary instance, also for command lines forwarded
            # by later launches (their cwd may differ from ours).
            _mark("command-line")
            args = list(cmdline.get_arguments())[1:]
            try:
                ns, _unknown = build_parser().parse_known_args(args)
            except SystemExit:
                return 1
            cwd = cmdline.get_cwd() or os.getcwd()
            win_first = self.window is None
            self._show([os.path.normpath(os.path.join(cwd, p)) for p in ns.paths])
            if ns.date is not None and win_first:
                self.window.date_entry.set_text(format_user_date(ns.date))
            return 0

        def _show(self, paths):
            first = self.window is None
            win = self._win()
            win.present()
            if is_win and not first:  # files handed over by a later launch
                win_bring_to_front(APP_NAME)
            if paths:
                win.enqueue(list(paths))
            if first:
                GLib.idle_add(win.first_shown)

    if is_win:
        win_allow_foreground()
    return App().run(argv)


# ---------------------------------------------------------------------------


def main() -> int:
    _mark("main")
    # PyInstaller --windowed builds have no console streams.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    # Redirected output uses the legacy code page on Windows: a file name or
    # a "…" it cannot encode must not abort the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    settings = Settings()
    setup_i18n(settings.get("language"))
    args = build_parser().parse_args()

    if args.check_update:
        return run_check_update()
    if args.register or args.unregister:
        if sys.platform != "win32":
            print(_("Option available on Windows only."), file=sys.stderr)
            return 2
        if args.register:
            win_register()
            print(_("Context-menu entry added for the current user."))
        else:
            win_unregister()
            print(_("Context-menu entry removed for the current user."))
        return 0
    if args.paths and not args.gui:
        return run_cli(args.paths, args.date or date.today(), args.overwrite, args.out,
                       args.every_date, args.whole_page, args.dry_run, args.all_pages)
    return run_gui(sys.argv, settings)


if __name__ == "__main__":
    sys.exit(main())
