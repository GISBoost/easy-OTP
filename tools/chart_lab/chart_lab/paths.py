"""sys.path wiring so chart_lab can import its sibling standalone tools by path.

Same pattern as `transit_charts/extract.py` uses for `family_a`: these are sibling
tools under `tools/`, each with its own venv, imported by path rather than installed.
Kept in its own module so a later frozen build (PyInstaller, CL-6) has one obvious
place to adjust import resolution.
"""
from __future__ import annotations

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[2]

for _name in ("transit_charts", "family_a_reconstruction"):
    _path = _TOOLS / _name
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
