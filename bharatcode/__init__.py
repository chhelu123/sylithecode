"""Sylithe Code — AI Coding Agent for Indian Developers"""
__version__ = "0.1.0"

# Force UTF-8 output streams so emoji/Unicode never crash on cp1252 consoles
# (legacy Windows terminals, piped output, CI). Must run before any rich
# Console is created.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
