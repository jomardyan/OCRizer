"""Keep installed Python dependency license notices with desktop distributions."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    root = args.destination / "licenses" / "python"
    root.mkdir(parents=True, exist_ok=True)
    inventory = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name", "unknown")
        version = distribution.version
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        destination = root / safe_name
        destination.mkdir(exist_ok=True)
        metadata = distribution.read_text("METADATA") or distribution.read_text("PKG-INFO")
        if metadata is None:
            metadata = f"Name: {name}\nVersion: {version}\n"
        (destination / "METADATA").write_text(metadata, encoding="utf-8")
        for entry in distribution.files or ():
            if not any(
                part.lower().startswith(("license", "licence", "copying", "notice")) for part in entry.parts
            ):
                continue
            source = Path(distribution.locate_file(entry))
            if source.is_file():
                # Include parent names so distributions with several LICENSE files keep all of them.
                filename = "__".join(re.sub(r"[^A-Za-z0-9_.-]", "_", part) for part in entry.parts)
                shutil.copy2(source, destination / filename)
        inventory.append({"name": name, "version": version})
    (root.parent / "python-packages.json").write_text(
        json.dumps(sorted(inventory, key=lambda item: item["name"].lower()), indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
