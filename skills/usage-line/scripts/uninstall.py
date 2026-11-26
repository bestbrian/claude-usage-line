#!/usr/bin/env python3
"""Remove the usage status line from one or more Claude Code config folders.

It removes "statusLine" from settings.json only when it is the one install.py set, and backs
the file up first. The script and saved numbers in ~/.claude/statusline stay unless --purge.

Usage:
    python3 uninstall.py [--config-dir DIR ...] [--purge]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

# `python3 -I` leaves the script's own folder off the import path, so add it for install.py.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from install import HOME, STATUS_LINE, TARGET_DIR, backup  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Remove the Claude usage status line.")
    parser.add_argument("--config-dir", nargs="*", default=[str(HOME / ".claude")])
    parser.add_argument("--purge", action="store_true", help="also delete ~/.claude/statusline")
    args = parser.parse_args(argv)
    for raw in args.config_dir:
        settings_path = pathlib.Path(raw).expanduser() / "settings.json"
        try:
            settings = json.loads(settings_path.read_text())
        except (OSError, ValueError):
            print(f"{settings_path}: nothing to do")
            continue
        if settings.get("statusLine") != STATUS_LINE:
            print(f"{settings_path}: status line is not this one, left alone")
            continue
        kept = backup(settings_path, dry_run=False)
        settings.pop("statusLine")
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        print(f"{settings_path}: status line removed (backup {kept})")
    if args.purge and TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
        print(f"{TARGET_DIR}: deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
