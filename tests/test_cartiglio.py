#!/usr/bin/env python3
"""Standalone tests for the Cartiglio core (needs PyMuPDF, nothing else).

Run:  python tests/test_cartiglio.py
Also used by CI to generate a smoke-test sample:
      python tests/test_cartiglio.py --make-sample out.pdf
      python tests/test_cartiglio.py --check-sample changed.pdf
"""

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cartiglio as ct  # noqa: E402

NEW = date(2026, 10, 16)


# --- sample PDFs (built with PyMuPDF itself) ----------------------------------


def _pm():
    return ct.load_engine()


def drawing(rotate=0, scale_x=1.0, extra=()) -> bytes:
    """An A1 landscape sheet: a plan in the middle with a dated note in the
    top-left corner, and a title block bottom right with three current dates
    (14/08/2026) and an earlier revision (02/03/2026)."""
    pm = _pm()
    doc = pm.open()
    page = doc.new_page(width=2384, height=1684)
    page.draw_rect(pm.Rect(200, 200, 1800, 1300), color=(0, 0, 0), width=2)
    for x in range(250, 1800, 100):  # some line work to keep intact
        page.draw_line((x, 220), (x + 60, 1280), color=(0.2, 0.2, 0.8), width=0.8)
    page.insert_text((60, 80), "RILIEVO DEL 12/05/2025", fontsize=10, fontname="helv")
    page.insert_text((60, 100), "GAS FK-5-1-12  QUADRO 50-51-51N  31/02/2026", fontsize=10,
                     fontname="helv")
    # title block
    page.draw_rect(pm.Rect(1900, 1350, 2360, 1660), color=(0, 0, 0), width=1)
    labels = [("REV", 1910, 1380), ("DESCRIZIONE", 1950, 1380), ("DATA", 2290, 1380),
              ("REDAZIONE", 1910, 1560), ("CONTROLLO", 1910, 1590),
              ("APPROVAZIONE", 1910, 1620), ("SCALA", 2200, 1560)]
    for text, x, y in labels:
        page.insert_text((x, y), text, fontsize=9, fontname="helv")
    morph = None

    def put(x, y, text):
        m = (pm.Point(x, y), pm.Matrix(scale_x, 1)) if scale_x != 1.0 else morph
        page.insert_text((x, y), text, fontsize=9, fontname="helv", morph=m)

    put(1910, 1410, "00")
    put(1950, 1410, "PRIMA EMISSIONE")
    put(2290, 1410, "02/03/2026")
    put(1910, 1440, "01")
    put(1950, 1440, "AGGIORNAMENTO")
    put(2290, 1440, "14/08/2026")
    put(2050, 1560, "14/08/2026")
    put(2050, 1590, "14/08/2026")
    put(2050, 1620, "DATA: 14/08/2026 - OK")
    for x, y, text in extra:
        put(x, y, text)
    if rotate:
        page.set_rotation(rotate)
    out = doc.tobytes()
    doc.close()
    return out


def report(pages=3) -> bytes:
    """An A4 report: a cover with the title block, dates in other formats,
    and a date on page 2 that is not part of any title block."""
    pm = _pm()
    doc = pm.open()
    cover = doc.new_page(width=595, height=842)
    cover.insert_text((100, 200), "RELAZIONE TECNICA", fontsize=20, fontname="helv")
    cover.insert_text((100, 600), "REDAZIONE", fontsize=9, fontname="helv")
    cover.insert_text((250, 600), "14.08.26", fontsize=9, fontname="helv")
    cover.insert_text((100, 620), "CONTROLLO", fontsize=9, fontname="helv")
    cover.insert_text((250, 620), "14/8/2026", fontsize=9, fontname="helv")
    cover.insert_text((100, 640), "EMISSIONE", fontsize=9, fontname="helv")
    cover.insert_text((250, 640), "14-08-2026", fontsize=9, fontname="tiro")
    for n in range(1, pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Pagina {n + 1}: verbale del 14/08/2026", fontsize=11)
    out = doc.tobytes()
    doc.close()
    return out


def diagrams(sheets=6, old="29/05/2026") -> bytes:
    """A set of single-line diagrams: an A4 cover with its title block, then
    A4 landscape sheets each with a small block in the corner (DATA, ELAB.,
    FOGLIO). Sheet 2 also has a legend table headed CODICE / DATA /
    DESCRIZIONE at the top right, which outnumbers the corner's labels."""
    pm = _pm()
    doc = pm.open()
    cover = doc.new_page(width=595, height=842)
    cover.insert_text((100, 600), "REDAZIONE", fontsize=9, fontname="helv")
    cover.insert_text((250, 600), "14/08/2026", fontsize=9, fontname="helv")
    for n in range(sheets):
        p = doc.new_page(width=842, height=595)
        p.draw_line((60, 300), (700, 300), color=(0, 0, 0), width=1)
        p.insert_text((640, 535), "DATA", fontsize=5, fontname="helv")
        p.insert_text((693, 546), old, fontsize=6.4, fontname="helv")
        p.insert_text((640, 560), "ELAB.", fontsize=5, fontname="helv")
        p.insert_text((760, 535), "FOGLIO", fontsize=5, fontname="helv")
        p.insert_text((760, 545), str(n + 1), fontsize=6, fontname="helv")
        if n == 1:
            for k, label in enumerate(("CODICE", "DATA", "DESCRIZIONE", "CODICE", "DATA",
                                       "DESCRIZIONE", "PROGETTO", "TITOLO")):
                p.insert_text((450 + 45 * (k % 4), 60 + 12 * (k // 4)), label,
                              fontsize=6, fontname="helv")
            p.insert_text((500, 100), "CEI 64-8 del 01/06/2024", fontsize=6, fontname="helv")
    out = doc.tobytes()
    doc.close()
    return out


def leading_block() -> bytes:
    """A title block written the way Word and AutoCAD often do: lines moved
    with the leading (TL, T*, ', ", TD) rather than explicit offsets."""
    pm = _pm()
    content = (b"q BT /F1 10 Tf 17 TL 100 700 Td (REDAZIONE 14/08/2026) Tj "
               b"(CONTROLLO 14/08/2026)' 0 0 (APPROVAZIONE 14/08/2026)\" T* (DATA) Tj "
               b"0 -20 TD (EMISSIONE 14/08/2026) Tj (FINE)' ET Q "
               b"BT /F1 10 Tf 300 700 Td (SCALA 1:50)' ET")
    doc = pm.open()
    page = doc.new_page(width=595, height=842)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<< >>")
    doc.update_stream(xref, content)
    font = doc.get_new_xref()
    doc.update_object(font, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    doc.xref_set_key(page.xref, "Resources", f"<< /Font << /F1 {font} 0 R >> >>")
    out = doc.tobytes()
    doc.close()
    return out


def lines(path, index=0):
    """(y, text) of every line of a page, rounded."""
    pm = _pm()
    doc = pm.open(path)
    try:
        return [(round(line["bbox"][1]), "".join(s["text"] for s in line["spans"]).strip())
                for b in doc[index].get_text("dict")["blocks"] for line in b.get("lines", ())]
    finally:
        doc.close()


def page_text(path, index=0):
    pm = _pm()
    doc = pm.open(path)
    try:
        return doc[index].get_text()
    finally:
        doc.close()


def span_widths(path, needle):
    """Width of every span whose text is `needle` (displayed coordinates)."""
    pm = _pm()
    doc = pm.open(path)
    try:
        page = doc[0]
        return [s["bbox"][2] - s["bbox"][0] if abs(line["dir"][0]) > 0.5
                else s["bbox"][3] - s["bbox"][1]
                for b in page.get_text("dict")["blocks"] for line in b.get("lines", ())
                for s in line["spans"] if s["text"].strip() == needle]
    finally:
        doc.close()


def expect_raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--make-sample":
        Path(sys.argv[2]).write_bytes(drawing())
        print(f"sample written: {sys.argv[2]}")
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "--check-sample":
        text = page_text(sys.argv[2])
        assert "14/08/2026" not in text and text.count(ct.format_user_date(date.today())) == 4, text
        assert "02/03/2026" in text
        print("sample changed as expected")
        return 0

    # --- dates: what is one, and how it is written again ----------------------
    found = [m.group(0) for m in ct._DATE_RE.finditer(
        "14/08/2026 4.8.26 FK-5-1-12 50-51-51N 14/08/20261 a14/08/2026 1/2/3/2026 31-12-1999")]
    assert found == ["14/08/2026", "4.8.26", "31-12-1999"], found
    m = ct._DATE_RE.search("31/02/2026")
    assert ct.parse_date_match(m) is None  # not a calendar date
    value, style = ct.parse_date_match(ct._DATE_RE.search("4.8.26"))
    assert value == date(2026, 8, 4) and style.format(NEW) == "16.10.26"
    assert ct.parse_date_match(ct._DATE_RE.search("04/08/2026"))[1].format(date(2027, 1, 2)) == "02/01/2027"
    assert ct.parse_date_match(ct._DATE_RE.search("4/8/2026"))[1].format(date(2027, 1, 2)) == "2/1/2027"
    assert ct.parse_user_date("16/10/2026") == NEW == ct.parse_user_date("2026-10-16")
    assert ct.parse_user_date(" 16.10.26 ") == NEW
    for bad in ("16/13/2026", "", "domani", "2026-02-30"):
        expect_raises(ValueError, ct.parse_user_date, bad)
    assert ct.format_user_date(NEW) == "16/10/2026" and ct.date_label(NEW) == "16-10-2026"

    # --- fonts: names to installed files / standard fallbacks -------------------
    assert ct._font_key("HVUAQN+Arial-BoldMT") == "arialbold"
    assert ct._font_key("ArialNarrow") == "arialnarrow" == ct._font_key("Arial Narrow")
    assert ct._font_key("TimesNewRomanPSMT") == "timesnewroman"
    assert ct._split_family("ArialNarrow-Bold") == ("Arial Narrow", "Bold")
    assert ct._split_family("ABCDEF+ArialMT") == ("Arial", "Regular")
    assert ct._base14("Arial-BoldItalicMT") == "hebi" and ct._base14("TimesNewRomanPSMT") == "tiro"
    assert ct._base14("CourierNewPSMT") == "cour" and ct._base14("Helvetica") == "helv"

    # --- title block zone ------------------------------------------------------
    W, H = 2384, 1684
    words = [(2100, 1500, 2130, 1510, "DATA"), (2000, 1550, 2060, 1560, "REDAZIONE"),
             (100, 100, 130, 110, "DATA")]  # a lone label elsewhere does not move it
    x0, y0, x1, y1, guessed = ct.title_block_zone((W, H), words, False)
    assert not guessed and x1 == W and y1 == H and x0 >= W / 2 and y0 >= H / 2
    assert x0 < 2000 and y0 < 1500
    *_zone, guessed = ct.title_block_zone((W, H), [], False)
    assert guessed
    assert ct.title_block_zone((595, 842), [], True) == (0.0, 0.0, 595, 842, False)

    ct.load_engine()  # a clear failure here beats twenty confusing ones below

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        # --- a drawing: only the title block, only the latest date ---------------
        f = td / "tavola.pdf"
        original = drawing()
        f.write_bytes(original)
        a = ct.analyse_file(f)
        assert not a.is_document and not a.zone_guessed and a.pages == 1
        texts = {d.text: d for d in a.dates}
        assert set(texts) == {"12/05/2025", "02/03/2026", "14/08/2026"}, texts
        assert not texts["12/05/2025"].in_block            # the note on the plan
        assert len(a.block_dates) == 5
        chosen = a.default_selection()
        assert {d.text for d in a.dates if d.index in chosen} == {"14/08/2026"} and len(chosen) == 4
        assert "5 dates in the title block" in ct.describe(a) and "4 to change" in ct.describe(a)
        dest = td / "out" / "tavola.pdf"
        seen = []
        res = ct.redate_file(a, dest, NEW, progress=seen.append)
        assert res.dest == dest and res.changed == 4
        assert seen == sorted(seen) and seen[-1] == 1.0
        text = page_text(dest)
        assert "14/08/2026" not in text and text.count("16/10/2026") == 4, text
        assert "02/03/2026" in text and "12/05/2025" in text      # history and notes kept
        assert "DATA: 16/10/2026 - OK" in text.replace("\n", " ") or (
            "DATA:" in text and "- OK" in text), text                 # neighbours kept
        assert "FK-5-1-12" in text and "31/02/2026" in text
        assert f.read_bytes() == original                             # original untouched
        expect_raises(FileExistsError, ct.redate_file, a, dest, NEW)
        # every date in the block, older ones included, when asked
        res = ct.redate_file(a, dest, NEW, {d.index for d in a.block_dates}, overwrite=True)
        text = page_text(dest)
        assert res.changed == 5 and "02/03/2026" not in text and "12/05/2025" in text
        # nothing selected
        expect_raises(ct.NothingToChange, ct.redate_file, a, td / "none.pdf", NEW, set())
        expect_raises(ct.CartiglioError, ct.redate_file, a, f, NEW, overwrite=True)  # onto itself

        # --- the before/after check stops a damaged page ---------------------------
        real = ct._replace_on_page

        def sabotage(pymupdf, doc, page, hits, new_date):
            texts = real(pymupdf, doc, page, hits, new_date)
            page.draw_line((300, 300), (900, 900), color=(0, 0, 0), width=3)
            return texts

        ct._replace_on_page = sabotage
        try:
            expect_raises(ct.CartiglioError, ct.redate_file, a, td / "bad.pdf", NEW)
        finally:
            ct._replace_on_page = real
        assert not (td / "bad.pdf").exists() and not list(td.rglob("*.part"))

        # --- the new date takes the room of the old one (AutoCAD width factor) -----
        for scale_x in (0.7, 1.0):
            g = td / f"narrow{scale_x}.pdf"
            g.write_bytes(drawing(scale_x=scale_x))
            before = span_widths(g, "14/08/2026")
            res = ct.redate_file(ct.analyse_file(g), td / "out" / g.name, NEW)
            after = span_widths(res.dest, "16/10/2026")
            # (the date inside "DATA: … - OK" becomes a span of its own: 3 + 1)
            assert len(after) == 4 and before, (before, after)
            assert all(abs(w - before[0]) < 0.06 * before[0] for w in after), (scale_x, before, after)

        # --- rotated sheets ----------------------------------------------------------
        for angle in (90, 180, 270):
            g = td / f"rot{angle}.pdf"
            g.write_bytes(drawing(rotate=angle))
            a = ct.analyse_file(g)
            assert len(a.default_selection()) == 4, (angle, ct.describe(a))
            res = ct.redate_file(a, td / "out" / g.name, NEW)
            text = page_text(res.dest)
            assert text.count("16/10/2026") == 4 and "02/03/2026" in text, angle

        # --- a report: the whole cover, formats kept, other pages untouched ----------
        r = td / "relazione.pdf"
        r.write_bytes(report())
        a = ct.analyse_file(r)
        assert a.is_document and a.pages == 3
        assert {d.text for d in a.block_dates} == {"14.08.26", "14/8/2026", "14-08-2026"}
        res = ct.redate_file(a, td / "out" / r.name, NEW)
        text = page_text(res.dest)
        assert "16.10.26" in text and "16/10/2026" in text and "16-10-2026" in text, text
        assert "14/08/2026" in page_text(res.dest, 1)   # page 2 is not the title block
        pm = ct.load_engine()
        with pm.open(res.dest) as doc:
            assert doc.page_count == 3

        # --- lines moved with the leading (MuPDF 1.28 mangles them on its own) --------
        moves, end = ct.explicit_line_moves(
            b"q 12 TL BT (a)' 1 2 (b)\" T* 3 -4 TD T* (c)Tj ET Q T*", 0.0)
        assert moves == (b"q 12 TL BT 0 -12 Td (a) Tj 1 Tw 2 Tc 0 -12 Td (b) Tj 0 -12 Td "
                         b"4 TL 3 -4 Td 0 -4 Td (c)Tj ET Q 0 0 Td"), moves
        assert ct.explicit_line_moves(b"BT /F1 9 Tf (x) Tj ET")[0] is None
        # strings, hex strings, names and inline images are copied as they are
        tricky = b"BT (it's \\) T* (x)') Tj <2754> Tj ET BI /W 1 ID \x00T*' EI 5 TL T*"
        moves, _end = ct.explicit_line_moves(tricky)
        assert moves.startswith(b"BT (it's \\) T* (x)') Tj <2754> Tj ET BI /W 1 ID \x00T*' EI"), moves
        assert moves.endswith(b"5 TL 0 -5 Td"), moves
        q = td / "leading.pdf"
        q.write_bytes(leading_block())
        before_lines = lines(q)
        a = ct.analyse_file(q)
        assert len(a.default_selection()) == 4, ct.describe(a)
        res = ct.redate_file(a, td / "out" / q.name, NEW)
        after_lines = lines(res.dest)
        # every line where it was, the new dates (lines of their own) where the old ones were
        assert [(y, t.replace("14/08/2026", "").strip()) for y, t in before_lines] == \
            [(y, t) for y, t in after_lines if t != "16/10/2026"], (before_lines, after_lines)
        # (top of the box: the substitute font found may have a taller ascender
        # than the original, on the same baseline)
        old_y = sorted(y for y, t in before_lines if "14/08/2026" in t)
        new_y = sorted(y for y, t in after_lines if t == "16/10/2026")
        assert len(old_y) == len(new_y) and all(abs(a - b) <= 3 for a, b in zip(old_y, new_y)), \
            after_lines

        # --- a report does not take its later pages along ----------------------------
        assert a.later_pages == 0 and not a.all_pages

        # --- single-line diagrams: the block repeated on every sheet -----------------
        s = td / "unifilari.pdf"
        s.write_bytes(diagrams())
        a = ct.analyse_file(s)
        assert a.repeated and a.all_pages and a.later_pages == 6, ct.describe(a)
        later = [d for d in a.dates if d.page > 0]
        assert len(later) == 6 and {d.text for d in later} == {"29/05/2026"}  # not the CEI date
        assert "6 more pages with a title block (29/05/2026)" in ct.describe(a)
        res = ct.redate_file(a, td / "out" / s.name, NEW)
        assert res.changed == 7
        for n in range(1, 7):
            text = page_text(res.dest, n)
            assert "16/10/2026" in text and "29/05/2026" not in text, (n, text)
        assert "01/06/2024" in page_text(res.dest, 2)
        before_png, after_png = ct.preview_png(a, NEW, page=3)
        assert after_png.startswith(b"\x89PNG")
        # ...unless told to stay on the first page, or the sheets differ
        a = ct.analyse_file(s, all_pages=False)
        assert not a.all_pages and len(a.default_selection()) == 1
        res = ct.redate_file(a, td / "out" / "first.pdf", NEW)
        assert "29/05/2026" in page_text(res.dest, 1)
        a = ct.analyse_file(s)
        a.all_pages = False   # the GUI switch
        assert len(a.default_selection()) == 1 and "not included" in ct.describe(a)

        # --- previews: before and after pictures, nothing written --------------------
        before_png, after_png = ct.preview_png(a, NEW)
        assert before_png.startswith(b"\x89PNG") and after_png.startswith(b"\x89PNG")
        assert ct.preview_png(a, NEW, set())[1] is None

        # --- no dates, not a PDF, password -------------------------------------------
        blank = td / "blank.pdf"
        doc = pm.open()
        doc.new_page()
        doc.save(blank)
        doc.close()
        a = ct.analyse_file(blank)
        assert not a.dates and ct.describe(a) == "no date on the first page"
        expect_raises(ct.NothingToChange, ct.redate_file, a, td / "x.pdf", NEW)
        junk = td / "junk.pdf"
        junk.write_bytes(b"this is not a PDF at all\n" * 20)
        expect_raises(ct.CartiglioError, ct.analyse_file, junk)
        locked = td / "locked.pdf"
        doc = pm.open(stream=drawing(), filetype="pdf")
        doc.save(locked, encryption=pm.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
        doc.close()
        expect_raises(ct.CartiglioError, ct.analyse_file, locked)

        # --- names with spaces, plus signs and accents ---------------------------------
        odd = td / "RC_ie06_ill.+fm_ piano terrà-RC_ie06.pdf"
        odd.write_bytes(drawing())
        res = ct.redate_file(ct.analyse_file(odd), td / "out" / odd.name, NEW)
        assert res.dest.read_bytes().startswith(b"%PDF-")

        # --- where the output goes ------------------------------------------------------
        root = td / "consegna"
        assert ct.output_path(root / "ELE" / "a.pdf", root, NEW) == \
            td / "consegna - 16-10-2026" / "ELE" / "a.pdf"
        assert ct.output_path(td / "a.pdf", None, NEW) == td / "Cartiglio 16-10-2026" / "a.pdf"
        assert ct.output_path(root / "ELE" / "a.pdf", root, NEW, td / "x") == td / "x" / "ELE" / "a.pdf"
        assert ct.output_path(td / "a.pdf", None, NEW, td / "x") == td / "x" / "a.pdf"

        # --- folder scan and CLI -----------------------------------------------------------
        (root / "ELE").mkdir(parents=True)
        (root / "ELE" / "T1.PDF").write_bytes(drawing())
        (root / "R.pdf").write_bytes(report(1))
        (root / "ELE" / "notes.txt").write_text("x")
        found = ct.iter_pdf([root])
        assert [p.name for p, _r in found] == ["T1.PDF", "R.pdf"] or \
            sorted(p.name for p, _r in found) == ["R.pdf", "T1.PDF"]
        assert all(r == root for _p, r in found)
        assert ct.run_cli([root], NEW, False, None, False, False, True) == 0      # dry run
        assert not (td / "consegna - 16-10-2026").exists()
        assert ct.run_cli([root], NEW, False, None, False, False, False) == 0
        out = td / "consegna - 16-10-2026"
        assert (out / "ELE" / "T1.PDF").is_file() and (out / "R.pdf").is_file()
        assert ct.run_cli([root], NEW, False, None, False, False, False) == 0     # all skipped
        (root / "bad.pdf").write_bytes(b"nope")
        assert ct.run_cli([root], NEW, True, None, False, False, False) == 1      # one error
        assert ct.run_cli([td / "missing"], NEW, False, None, False, False, False) == 1

    # --- update check helpers (no network) ---------------------------------
    assert ct.parse_version("v1.10.2") == (1, 10, 2)
    assert ct.parse_version("2") == (2,)
    assert ct.is_newer("1.2.1", "1.2.0") and not ct.is_newer("1.2.0", "1.2.0")
    assets = [{"name": "cartiglio-v1.3.0-windows-x64-portable.zip"},
              {"name": "cartiglio-setup-v1.3.0-windows-x64.exe"},
              {"name": "cartiglio-v1.3.0-linux-x64-portable.tar.gz"}]
    assert ct.pick_asset(assets, installed=True)["name"].endswith(".exe")
    assert ct.pick_asset(assets, installed=False)["name"].endswith(".zip")
    assert ct.pick_asset([], installed=True) is None

    # --- preferences round-trip ----------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        ini = Path(td) / "cfg" / "settings.ini"
        s = ct.Settings(ini)
        assert s.get_bool("check_updates") and s.get("last_update_check") == ""
        s.set("check_updates", False)
        again = ct.Settings(ini)
        assert not again.get_bool("check_updates")
        assert again.get_bool("native_decorations")  # untouched default survives

    # --- command line -----------------------------------------------------------
    ns, unknown = ct.build_parser().parse_known_args(["--gui", "a.pdf", "--future-flag"])
    assert ns.gui and ns.paths == ["a.pdf"] and unknown == ["--future-flag"]
    ns = ct.build_parser().parse_args(["--date", "16.10.2026", "--all", "x.pdf"])
    assert ns.date == NEW and ns.every_date and not ns.whole_page
    import contextlib
    import io
    for bad in (["--date", "32/10/2026"], ["--date", "ieri"]):
        with contextlib.redirect_stderr(io.StringIO()):
            expect_raises(SystemExit, ct.build_parser().parse_args, bad)

    # --- translations: po -> mo compiler and gettext plumbing ----------------
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import compile_po  # noqa: E402
    root = Path(compile_po.ROOT)
    with tempfile.TemporaryDirectory() as td:
        written = compile_po.compile_all(root / "po", Path(td))
        assert [lang for lang, _count, _out in written] == ["it"]
        import gettext
        it = gettext.translation(ct.TEXTDOMAIN, td, languages=["it"])
        assert it.gettext("Queued") == "In coda"
        assert it.gettext("not a translated string") == "not a translated string"
    # every msgid in the catalogue must exist in the source, and every
    # translatable string of the source must be in the catalogue (adjacent
    # string literals split over lines are joined first)
    import re
    source = re.sub(r'"\s*\n\s*"', "", (root / "cartiglio.py").read_text(encoding="utf-8"))
    entries = compile_po.parse_po(root / "po" / "it.po")
    missing = [m for m in entries if m and m not in source]
    assert not missing, f"msgids not found in the source: {missing[:5]}"
    used = set(re.findall(r'\b_\("((?:[^"\\]|\\.)*)"\)', source))
    used = {u.encode().decode("unicode_escape").encode("latin-1").decode("utf-8") for u in used}
    untranslated = sorted(used - set(entries))
    assert not untranslated, f"strings missing from po/it.po: {untranslated[:5]}"
    # placeholders must survive translation
    for msgid, msgstr in entries.items():
        if msgid:
            assert sorted(re.findall(r"\{\w+\}", msgid)) == sorted(re.findall(r"\{\w+\}", msgstr)), msgid
    assert ct.detect_language() in ct.LANGUAGES

    # --- icons: gdk-pixbuf must be able to sniff them ------------------------
    # It only looks for the <svg> element in the first 256 bytes; past that the
    # file is "not a valid icon" and `flatpak build-export` fails.
    icons = sorted((root / "data" / "icons").rglob("*.svg"))
    assert icons, "no icon SVGs found"
    for icon in icons:
        head = icon.read_bytes()
        offset = head.find(b"<svg")
        assert 0 <= offset <= 256, (
            f"{icon.name}: <svg> starts at byte {offset}; gdk-pixbuf only sniffs "
            "the first 256 bytes, so flatpak-builder would reject this icon")

    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
