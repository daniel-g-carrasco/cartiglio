"""The PDF engine (PyMuPDF) and a file read into it."""

from __future__ import annotations

from pathlib import Path

from cartiglio.core.model import CartiglioError
from cartiglio.i18n import _

_CHUNK = 1 << 20               # 1 MiB: I/O granularity for progress reporting


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


_gap_miscounted: bool | None = None     # asked once per run: see gap_miscounted


def gap_miscounted(pymupdf) -> bool:
    """Does this engine miscount the gap a redaction leaves in the middle of
    a string of a TJ array? MuPDF 1.28.6 (PyMuPDF 2.1) does: the text that
    follows on the line comes one glyph closer. Asked of the engine itself,
    on a line made for the purpose, rather than read off a version number:
    the versions before and after are not all known. An engine that cannot
    be asked counts as one that miscounts: the remedy (the strings split,
    redate.py) draws the same page on any engine."""
    global _gap_miscounted
    if _gap_miscounted is None:
        try:
            _gap_miscounted = _ask_about_the_gap(pymupdf)
        except Exception:  # an engine too different to ask: the cautious answer
            _gap_miscounted = True
    return _gap_miscounted


def _ask_about_the_gap(pymupdf) -> bool:
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=300, height=100)
        font = doc.get_new_xref()
        doc.update_object(font, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        doc.xref_set_key(page.xref, "Resources", f"<< /Font << /F {font} 0 R >> >>")
        contents = doc.get_new_xref()
        doc.update_object(contents, "<<>>")
        doc.update_stream(contents, b"BT /F 10 Tf 20 50 Td [(AB 12345 YZ)] TJ ET")
        doc.xref_set_key(page.xref, "Contents", f"{contents} 0 R")
        data = doc.tobytes()
    finally:
        doc.close()
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        page = doc[0]

        def characters():
            return [c for block in page.get_text("rawdict")["blocks"] for line in block.get("lines", [])
                    for span in line["spans"] for c in span["chars"]]

        before = characters()
        text = "".join(c["c"] for c in before)
        first, last = before[text.index("1")], before[text.index("5")]
        was = before[text.index("Y")]["origin"][0]
        page.add_redact_annot(pymupdf.Rect(first["bbox"][0], first["bbox"][1] + 2, last["bbox"][2],
                                           first["bbox"][3] - 2), fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        after = characters()
        text = "".join(c["c"] for c in after)
        if "Y" not in text or "1" in text:      # not the redaction this question is about
            return True
        return abs(after[text.index("Y")]["origin"][0] - was) > 0.1
    finally:
        doc.close()


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
