"""The names of fonts as PDFs write them, brought to what the installed
fonts and the PDF standard fonts are called.
"""

from __future__ import annotations

import re

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
