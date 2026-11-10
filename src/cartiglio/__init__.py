"""Cartiglio: change the dates in the title block of PDF drawings and documents.

The first page of every PDF is searched for dates (14/08/2026, 14.08.26 and
the like). Those inside the title block (the table in a corner of a
drawing, the cover of a report) are replaced by a new date. The old glyphs
are really removed from the page content, not hidden under a white box, and
the new date is written in the same place, font, size, colour, direction and
width: it stays text that can be searched and copied.

Before anything is saved the page is rendered again and compared with the
original: if anything changed outside the replaced dates, the file is left
alone. The result goes to a new folder; the original is never modified.

Run with file/folder arguments for CLI mode, or without arguments for the
GTK 4 GUI.

The package is in four layers, and the imports go one way only (tests/
test_strati.py holds them to it): core/ works things out (the dates, where
the title block is, what was found) and data/ rewrites the PDF's content
streams, both plain Python that know nobody; app/ is the services without
a window (a PDF analysed and redated with the PDF engine, the fonts,
settings, updates, Windows' registry) and joins the two; gui/ is the
window, the one place GTK comes in. The constants below are the app's
identity.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

__version__ = "1.1.6"

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

# the one anchor for files found from the code's own place (a checkout's
# data/, locale/, bin/): never a chain of .parent in a module, which a move breaks
PACKAGE_DIR = Path(__file__).resolve().parent


def _mark(label: str) -> None:
    """Start-up profiling aid: append a timestamp to $CARTIGLIO_STARTUP_LOG."""
    path = os.environ.get("CARTIGLIO_STARTUP_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{time.time():.3f} {label}\n")
        except OSError:
            pass
