#!/usr/bin/env python3
"""Canonical Denim entrypoint discovered by Fab7."""

from __future__ import annotations

import sys


def _main() -> int:
    sys.dont_write_bytecode = True
    from denim.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
