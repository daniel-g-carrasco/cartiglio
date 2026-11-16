"""Shared test helpers: put src/ on sys.path, tell the platform. Every test
module starts with `import _setup`.

This file is Cartiglio's own (it has no fingerprint). The common tests, the
copies of the model's comune/tests listed in impronte/comune.json, ask it
for ROOT, WINDOWS, PACKAGE and shared(name): they never name the package."""

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PACKAGE = "cartiglio"
WINDOWS = sys.platform == "win32"


def shared(name: str):
    """The common module of that name ("i18n", "app.updates"), as the
    common tests ask for it: cartiglio.<name>."""
    return importlib.import_module(f"{PACKAGE}.{name}")


# No test writes in the user's own folders (tests/_userfolders.py, a copy of
# the model's). They are watched as the environment names them now, before
# anything changes it: a run that writes next to the installed Cartiglio's
# own files (its settings.ini, its errors.log) fails at its exit. Then the
# tests get folders of their own: the four XDG variables (config, state,
# data, cache) point into one temporary folder, removed at the exit
# (app/paths.py lets them win on Windows too). The guard looks at folders,
# not at Windows' registry: no test of Cartiglio writes there (the
# context-menu entry, app/windows_shell.py, is never registered by a test).
import _userfolders  # noqa: E402
from cartiglio import EXE_NAME, SLUG  # noqa: E402

_userfolders.watch(SLUG, EXE_NAME)
_userfolders.isolate()

os.environ.setdefault("LANGUAGE", "en")        # the tests read the source strings
# the failures said as the app says them (__main__ names the log at start)
from cartiglio.app import log  # noqa: E402

log.setup(SLUG)
