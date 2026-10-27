"""PyInstaller entry point (a script, so the package imports stay absolute)."""

import sys

from gp_agent.__main__ import main

sys.exit(main())
