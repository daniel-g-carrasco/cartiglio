# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec. A spec file is required (instead of CLI flags)
# because the gi hook collects GTK 3 by default: hooksconfig is the only
# way to make it bundle the GTK 4 typelibs and libraries.
import os
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

WIN = sys.platform == 'win32'
ICON = 'assets/icon.ico' if WIN else None

# Application icons for the GTK icon theme (window icon, About dialog).
# PyInstaller's GLib runtime hook points XDG_DATA_DIRS at <bundle>/share.
datas = [('data/icons', 'share/icons')]
binaries = []

# The PDF engine: PyMuPDF (MuPDF), whose extension modules PyInstaller's own
# hook collects, plus its licence texts (dist-info). On Windows it is the
# MSYS2 package (mingw-w64-ucrt-x86_64-python-pymupdf), linked to the shared
# libmupdf of MSYS2: collect_dynamic_libs picks up what sits in the package,
# PyInstaller's dependency analysis the rest.
binaries += collect_dynamic_libs('pymupdf')
datas += collect_data_files('pymupdf')
for dist in ('pymupdf', 'PyMuPDF', 'fonttools'):
    try:
        datas += copy_metadata(dist)
    except Exception:  # not installed under that name: nothing to copy
        pass

# Translations, compiled from po/ by `python tools/compile_po.py` (CI does it
# before running PyInstaller).
LOCALE_DIR = os.path.join(SPECPATH, 'locale')
if os.path.isdir(LOCALE_DIR):
    datas.append((LOCALE_DIR, 'share/locale'))
else:
    print('WARNING: locale/ missing: run tools/compile_po.py first (UI will be English only)')

if WIN:
    prefix = Path(sys.prefix)  # MSYS2: C:/msys64/ucrt64

    # gdbus.exe lets GLib autolaunch a session bus on Windows, which
    # GApplication needs for its single-instance behaviour (a second launch
    # forwards its files to the running window). GLib looks for it next to
    # libgio-2.0-0.dll, i.e. in the bundle's _internal directory.
    gdbus = shutil.which('gdbus') or (
        str(prefix / 'bin' / 'gdbus.exe') if (prefix / 'bin' / 'gdbus.exe').is_file() else None)
    if gdbus:
        binaries.append((gdbus, '.'))
    else:
        print('WARNING: gdbus.exe not found: every launch will open a new window')

    # CA bundle for the update check: MSYS2's OpenSSL does not necessarily
    # read the Windows certificate store (see _ssl_context()).
    import ssl
    candidates = [
        prefix / 'etc' / 'ssl' / 'certs' / 'ca-bundle.crt',
        prefix / 'etc' / 'ssl' / 'cert.pem',
        Path(ssl.get_default_verify_paths().openssl_cafile or ''),
    ]
    for ca in candidates:
        if ca.is_file():
            datas.append((str(ca), 'certs'))
            if ca.name != 'ca-bundle.crt':
                print(f'NOTE: bundling {ca} as certs/{ca.name}; expected name is ca-bundle.crt')
            break
    else:
        print('WARNING: no CA bundle found: the update check may fail on TLS')

# Introspection modules the gi hook must collect. The Linux GUI is a
# libadwaita app; the Windows build stays on plain GTK (see cartiglio.py).
GI_VERSIONS = {'Gtk': '4.0', 'Gdk': '4.0'}
if not WIN:
    GI_VERSIONS['Adw'] = '1'

a = Analysis(
    ['cartiglio.py'],
    binaries=binaries,
    datas=datas,
    # imported lazily (load_engine(), _subset_font()), so declare them
    hiddenimports=['pymupdf', 'fontTools.subset', 'fontTools.ttLib'],
    hooksconfig={'gi': {'module-versions': GI_VERSIONS}},
    excludes=['tkinter', 'numpy', 'PIL'],
)

pyz = PYZ(a.pure)

# Windows: embed our own manifest, which declares per-monitor DPI awareness
# on top of PyInstaller's defaults (GTK would otherwise switch the process
# to per-monitor awareness only during its own initialisation).
MANIFEST = os.path.join(SPECPATH, 'build-aux', 'windows', 'cartiglio.manifest') if WIN else None

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name='cartiglio',
    console=False,
    icon=ICON,
    manifest=MANIFEST,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name='cartiglio',
)
