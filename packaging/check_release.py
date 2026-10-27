"""Reject a release tag that does not match the package's single version source."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "ocrizer" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"\n]+)"', source, re.MULTILINE)
    if not match or args.tag != f"v{match[1]}":
        parser.error(f"tag {args.tag!r} must equal v plus ocrizer.__version__")
    print(match[1])


if __name__ == "__main__":
    main()
