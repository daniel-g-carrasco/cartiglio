"""Where the title block of a page is, from the words on it."""

from __future__ import annotations

import re
from collections import Counter

# A drawing has its title block in one corner, where words like DATA,
# REDAZIONE, SCALA or REV crowd together; dates elsewhere on the sheet
# (notes, surveys, details) are left alone. A report has it on the cover, so
# there the whole first page counts.
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
