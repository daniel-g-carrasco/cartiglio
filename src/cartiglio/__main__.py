"""Entry point: the command line when files are given, the window otherwise."""

from __future__ import annotations

import os
import sys
from datetime import date

from cartiglio.app import log
from cartiglio.app.config import Settings
from cartiglio.app.services import setup_language
from cartiglio.app.windows_shell import win_register, win_unregister

from . import SLUG, _mark
from .cli import build_parser, run_check_update, run_cli
from .i18n import _


def main() -> int:
    _mark("main")
    # PyInstaller --windowed builds have no console streams.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    # Redirected output uses the legacy code page on Windows: a file name or
    # a "…" it cannot encode must not abort the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    log.setup(SLUG)
    settings = Settings()
    setup_language(settings.get("language"))
    args = build_parser().parse_args()

    if args.check_update:
        return run_check_update()
    if args.register or args.unregister:
        if sys.platform != "win32":
            print(_("Option available on Windows only."), file=sys.stderr)
            return 2
        if args.register:
            win_register()
            print(_("Context-menu entry added for the current user."))
        else:
            win_unregister()
            print(_("Context-menu entry removed for the current user."))
        return 0
    if args.paths and not args.gui:
        return run_cli(args.paths, args.date or date.today(), args.overwrite, args.out,
                       args.every_date, args.whole_page, args.dry_run, args.all_pages)
    # the window's errors kept in a file too (app/errorlog.py: errors.log in
    # the state folder, rotated past 1 MB): started from File Explorer's menu
    # or the Start menu nobody reads stderr, and the built app has none. The
    # command line above speaks in its terminal, as before.
    from cartiglio.app import errorlog
    from cartiglio.app.services import app_paths
    errorlog.install(errorlog.log_file(app_paths().state))
    from cartiglio.gui.application import run_gui           # the toolkit, only when a window is wanted
    return run_gui(sys.argv, settings)


if __name__ == "__main__":
    sys.exit(main())
