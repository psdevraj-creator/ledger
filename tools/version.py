"""Print the application version so the installer build can reuse it.

Keeping one version number avoids the installer and the app drifting apart.

Usage:  python tools/version.py
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "ledger.py"


def main() -> int:
    text = SOURCE.read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION = "([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("APP_VERSION not found in ledger.py")
    print(match.group(1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
