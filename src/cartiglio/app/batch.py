"""The PDF files of the folders given, and where the changed copy of each goes."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from cartiglio import APP_NAME
from cartiglio.core.dates import date_label


# --- batches ---------------------------------------------------------------


def iter_pdf(paths):
    """Expand files/folders into (pdf, root) pairs. Folders are scanned
    recursively; root is the folder given (for files, their own folder), so
    that the output can mirror the tree below it."""
    found = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            found.extend((x, p) for x in sorted(p.rglob("*"))
                         if x.is_file() and x.suffix.lower() == ".pdf")
        elif p.is_file():
            found.append((p, None))
    return found


def output_path(src: Path, root: Path | None, new_date: date, out_dir: Path | None = None) -> Path:
    """Where the changed copy of src goes, keeping its name:

    - a file from a folder that was given:  <folder> - 16-10-2026/<same subpath>
    - a file given on its own:              <its folder>/Cartiglio 16-10-2026/<name>
    - with out_dir:                         <out_dir>/<subpath below the folder given, or name>
    """
    src = Path(src)
    label = date_label(new_date)
    rel = src.relative_to(root) if root is not None else Path(src.name)
    if out_dir is not None:
        return Path(out_dir) / rel
    if root is not None:
        root = Path(root)
        return root.with_name(f"{root.name} - {label}") / rel
    return src.parent / f"{APP_NAME} {label}" / src.name
