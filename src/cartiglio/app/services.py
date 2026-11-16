"""Cartiglio's own instances of the services that know no app.

paths, log, errorlog, updates, minisign, win32, diagnostics (and i18n at
the root, atomic in data) are the shared modules: copies, byte for byte,
of the model's comune/ (app-template), listed with their fingerprints in
impronte/comune.json and held to them by tests/test_impronte.py. They are
never edited here: a change goes to the model and comes back with its
tools/impronte.py --apply. They take a name, an id, a version as
arguments, so that the same files serve every app of mine, GTK or Qt. This
is where they meet the names in the package's __init__
(tests/test_services.py)."""

from __future__ import annotations

import os
from pathlib import Path

from cartiglio import (
    EXE_NAME,
    GITHUB_REPO,
    PACKAGE_DIR,
    PUBLIC_KEY,
    SLUG,
    TEXTDOMAIN,
    WINDOWS,
    __version__,
)
from cartiglio.i18n import language, setup

from .paths import Paths

# the languages of the preferences besides "auto" (the desktop's): the
# catalogues in po/, and English, the source strings
LANGUAGES = ("en", "it")


def app_paths(environ=None) -> Paths:
    """Where Cartiglio keeps its files: on Windows under %LOCALAPPDATA%\\cartiglio
    (state, where errors.log is), on Linux under the XDG folders. The
    preferences stay where they always were (app/config.py)."""
    return Paths.from_env(SLUG, EXE_NAME, environ)


def setup_language(choice: str = "auto") -> str:
    """The catalogue for the preference's language ("auto": the desktop's,
    "it", "en"), and the language in use ("it" or "en"). On Windows, and
    when one is chosen, GTK's own strings (the dialogs' buttons, the file
    chooser) are told it too: they come from libintl, which reads LANGUAGE.
    Before any text is built."""
    setup(TEXTDOMAIN, PACKAGE_DIR.parent.parent, choice if choice in LANGUAGES else "")
    used = language() if language() in LANGUAGES else "en"
    if WINDOWS or choice in LANGUAGES:
        os.environ["LANGUAGE"] = used
    return used


def project():
    """Whose releases an update comes from (updates.py is loaded only when
    asked: on Linux the Flatpak brings the updates)."""
    from .updates import Project
    return Project(repo=GITHUB_REPO, slug=SLUG, exe_name=EXE_NAME, version=__version__, public_key=PUBLIC_KEY)


def installer_command(installer: Path) -> list[str]:
    """Setup as an update from the app starts it: the shared arguments, and
    /RELAUNCH=1, which Cartiglio's installer reads to open the app again
    at the end (installer/cartiglio.iss)."""
    from .updates import installer_command as shared
    return [*shared(installer), "/RELAUNCH=1"]
