"""The dates of a PDF replaced: the old glyphs removed and the new date
written in their place, each page compared with the original before the
copy is saved, and the pictures of the title block before and after.
"""

from __future__ import annotations

import math
import os
from collections import Counter
from datetime import date
from pathlib import Path

from cartiglio.core.model import Analysis, CartiglioError, NothingToChange, Result
from cartiglio.data.content_stream import explicit_line_moves, split_shown_strings
from cartiglio.i18n import _

from .analyse import _transform_rect
from .fonts import _writing_font
from .pdf import _open, gap_miscounted, load_engine, read_bytes

_VERIFY_PIXELS = 4_000_000     # per rendered page for the before/after check
_VERIFY_LEVEL = 64             # grey-level difference that counts as a change
_VERIFY_TOLERANCE = 40         # pixels allowed to differ (anti-aliasing noise)


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


def _split_shown_strings(doc, page) -> None:
    """Apply split_shown_strings to the content streams of a page, in order
    (the font in use carries over from one stream to the next), for the
    fonts of the page whose character codes have a known size: one byte in a
    simple font, two in a composite font with an Identity encoding."""
    code_bytes = {}
    for _xref, _ext, kind, _base, name, encoding, referencer in page.get_fonts(full=True):
        if referencer:                          # a font of a form, not of the page's own streams
            continue
        if kind in ("Type1", "MMType1", "TrueType", "Type3"):
            code_bytes[name] = 1
        elif kind == "Type0" and encoding in ("Identity-H", "Identity-V"):
            code_bytes[name] = 2
    font = None
    for xref in page.get_contents():
        data = doc.xref_stream(xref)
        if not data:
            continue
        new, font = split_shown_strings(data, code_bytes, font)
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
            if gap_miscounted(pymupdf):
                _split_shown_strings(doc, page)
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
