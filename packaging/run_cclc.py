"""Frozen-app entry point used by PyInstaller.

Kept separate from the package so the PyInstaller spec has a concrete script to
analyse; it simply delegates to :func:`cclc.main.main`.
"""

import sys

from cclc.main import main

if __name__ == "__main__":
    sys.exit(main())
