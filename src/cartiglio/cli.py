"""The command line: files and folders redated without a window, the
options, the update check printed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from cartiglio import __version__
from cartiglio.app.analyse import analyse_file
from cartiglio.app.batch import iter_pdf, output_path
from cartiglio.app.pdf import load_engine
from cartiglio.app.redate import redate_file
from cartiglio.app.updates import UpdateError, fetch_latest_release, is_newer
from cartiglio.core.dates import parse_user_date
from cartiglio.core.model import CartiglioError, NothingToChange, describe

from .i18n import _


def run_check_update() -> int:
    """`--check-update`: print the latest release. Exit 0 when GitHub answered
    (up to date or not), 3 when the network/TLS layer failed."""
    print(_("Installed version: {version}").format(version=__version__))
    try:
        rel = fetch_latest_release()
    except UpdateError as e:
        print(_("Update check failed: {error}").format(error=e), file=sys.stderr)
        return 0 if e.reached else 3
    if is_newer(rel["version"]):
        print(_("Available: {version}  ({url})").format(version=rel["version"], url=rel["url"]))
    else:
        print(_("Latest version: {version} (up to date)").format(version=rel["version"]))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_cli(paths, new_date: date, overwrite: bool, out_dir, every_date: bool,
            whole_page: bool, dry_run: bool, all_pages=None) -> int:
    files = iter_pdf(paths)
    if not files:
        print(_("No PDF files found."), file=sys.stderr)
        return 1
    try:
        load_engine()
    except CartiglioError as e:
        print(f"ERR   {e}", file=sys.stderr)
        return 2
    n_ok = n_skip = n_err = 0
    for src, root in files:
        try:
            analysis = analyse_file(src, all_pages=all_pages)
            if whole_page:
                for d in analysis.dates:
                    d.in_block = d.in_block or d.page == 0
            selection = ({d.index for d in analysis.block_dates} if every_date
                         else analysis.default_selection())
            found = describe(analysis, selection)
            if dry_run:
                print(f"{src}  ({found})")
                for d in analysis.dates:
                    if d.page > 0 and not analysis.all_pages:
                        continue
                    mark = "x" if d.index in selection else ("-" if d.in_block else " ")
                    where = _("title block") if d.in_block else _("elsewhere")
                    page = _("page {n}").format(n=d.page + 1)
                    print(f"   [{mark}] {d.index:3}  {d.text:12} {page:10} {where}")
                continue
            dest = output_path(src, root, new_date, out_dir)
            result = redate_file(analysis, dest, new_date, selection, overwrite)
            print(f"OK    {result.dest}  ({found})")
            n_ok += 1
        except FileExistsError as e:
            print(_("SKIP  {file}  (already exists: {name}; use --overwrite)").format(
                file=src, name=Path(str(e)).name))
            n_skip += 1
        except NothingToChange:
            print(_("SKIP  {file}  ({reason})").format(file=src, reason=describe(analysis)))
            n_skip += 1
        except (CartiglioError, OSError) as e:
            print(f"ERR   {src}  ({e})", file=sys.stderr)
            n_err += 1
    if not dry_run:
        print("\n" + _("Changed: {ok}  Skipped: {skipped}  Errors: {errors}").format(
            ok=n_ok, skipped=n_skip, errors=n_err))
    return 0 if n_err == 0 else 1


def _date_arg(text: str) -> date:
    try:
        return parse_user_date(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            _("not a valid date: {value} (use dd/mm/yyyy)").format(value=text)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cartiglio",
        description=_("Change the dates in the title block of PDF drawings and "
                      "documents, many files at once."),
        epilog=_("Without arguments the graphical interface (GTK 4) starts. "
                 "Examples: cartiglio --date 16/10/2026 folder/ | "
                 "cartiglio --dry-run drawing.pdf"),
    )
    parser.add_argument("paths", nargs="*", metavar=_("FILE_OR_FOLDER"),
                        help=_("PDF files or folders to scan (recursive)"))
    parser.add_argument("--date", type=_date_arg, default=None, metavar="DD/MM/YYYY",
                        help=_("the new date (default: today)"))
    parser.add_argument("--out", type=Path, default=None, metavar=_("FOLDER"),
                        help=_("write the changed files here, keeping the "
                               "sub-folders (default: a new folder next to the "
                               "originals, named after the date)"))
    parser.add_argument("--all", action="store_true", dest="every_date",
                        help=_("change every date in the title block, older "
                               "ones too (default: only those equal to the most "
                               "recent, so earlier revisions keep theirs)"))
    parser.add_argument("--whole-page", action="store_true",
                        help=_("treat every date on the first page as part of "
                               "the title block"))
    pages = parser.add_mutually_exclusive_group()
    pages.add_argument("--all-pages", action="store_const", const=True, dest="all_pages",
                       help=_("also change the title blocks of the later pages "
                              "(default: only when every sheet has one, as in "
                              "a set of single-line diagrams)"))
    pages.add_argument("--first-page", action="store_const", const=False, dest="all_pages",
                       help=_("only change the first page"))
    parser.add_argument("--dry-run", action="store_true",
                        help=_("only list the dates found, write nothing"))
    parser.add_argument("--overwrite", action="store_true",
                        help=_("overwrite files already in the output folder"))
    parser.add_argument("--gui", action="store_true",
                        help=_("force the graphical interface"))
    parser.add_argument("--check-update", action="store_true",
                        help=_("check whether a newer version exists and exit"))
    parser.add_argument("--register", action="store_true",
                        help=_("(Windows) add the entry to the File Explorer "
                               "context menu for the current user and exit"))
    parser.add_argument("--unregister", action="store_true",
                        help=_("(Windows) remove the context-menu entry and exit"))
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    return parser
