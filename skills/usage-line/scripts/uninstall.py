#!/usr/bin/env python3
"""Remove the usage status line from one or more Claude Code config folders.

It removes "statusLine" from settings.json only when it is the one install.py set, and backs
the file up first. The script and saved numbers in ~/.claude/statusline stay unless --purge,
which deletes only the files this tool made: accounts.json and anything else of yours stays.

Usage:
    python3 uninstall.py [--config-dir DIR ...] [--purge]
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys

# `python3 -I` leaves the script's own folder off the import path, so add it for install.py.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from install import HOME, STATUS_LINE, TARGET_DIR, backup, read_settings, write_settings  # noqa: E402

OWN_FOLDERS = ("usage", "readings", "cmux")
OWN_FILE_PATTERNS = ("usage.py", "usage.py.bak-usage-line-*", "account-*.json", "sessions.json",
                     "sessions.lock", "*.tmp")


def purge() -> None:
    """Delete what this tool created in ~/.claude/statusline, never the whole folder: other
    status line scripts often live there too."""
    if not TARGET_DIR.exists():
        return
    for name in OWN_FOLDERS:
        shutil.rmtree(TARGET_DIR / name, ignore_errors=True)
    for pattern in OWN_FILE_PATTERNS:
        for path in TARGET_DIR.glob(pattern):
            path.unlink()
    left = sorted(path.name for path in TARGET_DIR.iterdir())
    if left:
        print(f"{TARGET_DIR}: removed this tool's files, kept {', '.join(left)}")
    else:
        TARGET_DIR.rmdir()
        print(f"{TARGET_DIR}: deleted")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Remove the Claude usage status line.")
    parser.add_argument("--config-dir", nargs="*", default=[str(HOME / ".claude")])
    parser.add_argument("--purge", action="store_true",
                        help="also delete this tool's files in ~/.claude/statusline")
    args = parser.parse_args(argv)
    for raw in args.config_dir:
        settings_path = pathlib.Path(raw).expanduser() / "settings.json"
        settings, problem = read_settings(settings_path)
        if problem or not settings:
            print(f"{settings_path}: nothing to do")
            continue
        if settings.get("statusLine") != STATUS_LINE:
            print(f"{settings_path}: status line is not this one, left alone")
            continue
        kept = backup(settings_path.resolve(), dry_run=False)
        write_settings(settings_path, {key: value for key, value in settings.items() if key != "statusLine"})
        print(f"{settings_path}: status line removed (backup {kept})")
    if args.purge:
        purge()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
