"""Compatibility shim. The CLI lives in ``gather.cli`` so it ships with the package."""

from __future__ import annotations

import sys

from gather.cli import main

if __name__ == "__main__":
    sys.exit(main())
