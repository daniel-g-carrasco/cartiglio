"""A PDF analysed: the dates of its pages, which are in the title block,
whether the later pages have a title block of their own.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from cartiglio.core.dates import _DATE_RE, parse_date_match
from cartiglio.core.model import Analysis, CartiglioError, DateHit
from cartiglio.core.title_block import _DOCUMENT_MAX_SIDE, title_block_zone
from cartiglio.i18n import _

from .pdf import _is_signed, _open, load_engine, read_bytes


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
