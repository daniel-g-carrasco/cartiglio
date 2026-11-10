"""Entry point of the PyInstaller builds (PyInstaller freezes this file)."""

import sys

from cartiglio.__main__ import main

sys.exit(main())
