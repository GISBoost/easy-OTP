"""Every algorithm with an OTP jar parameter must default it to the path saved by Setup.

Source scan (algorithms need QGIS to import): regression guard for students who
ran 'Download Java 8 JRE and OpenTripPlanner Jar' and still had to point at the jar.
"""

from __future__ import annotations

import re
from pathlib import Path

ALGORITHMS = Path(__file__).parent.parent / "algorithms"


def test_jar_param_defaults_to_saved_path() -> None:
    checked = 0
    for path in ALGORITHMS.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for block in re.findall(r"QgsProcessingParameterFile\(\s*self\.OTP_JAR_PATH,.*?\n\s*\)", src, re.S):
            assert 'QSettings().value("easy_otp/otp_jar_path"' in block, path.name
            assert "optional=True" in block, path.name
            checked += 1
    assert checked >= 10
