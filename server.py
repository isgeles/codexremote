#!/usr/bin/env python3
"""Compatibility launcher; prefer the installed codexremote command."""
from codexremote.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
