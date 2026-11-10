"""The fonts to write the new date with: the installed font of the same
name (the Windows font registry, fontconfig elsewhere), reduced to the
glyphs a date needs, or the closest PDF standard font.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from cartiglio.core.font_names import _METRIC_ALIASES, _base14, _font_key, _split_family


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
