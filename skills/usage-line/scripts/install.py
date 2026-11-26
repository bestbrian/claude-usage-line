#!/usr/bin/env python3
"""Install the usage status line for one or more Claude Code config folders.

What it does, in order:
1. Copies usage.py to ~/.claude/statusline/usage.py. Every account shares this one folder,
   which is how each status line can show the other accounts' numbers.
2. For each config folder (default ~/.claude, or the --config-dir values), backs up
   settings.json and sets its "statusLine" to run that script.

Safe to run again: a folder already set up is left alone. A folder with a different status
line is refused unless --replace is given, so nobody loses a status line they built.
Backups go next to each file as <name>.bak-usage-line-<timestamp>.

Usage:
    python3 install.py                                  # ~/.claude only
    python3 install.py --config-dir ~/.claude-work ~/.claude-personal
    python3 install.py --replace                        # overwrite an existing status line
    python3 install.py --dry-run                        # say what would change, change nothing
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import time

HOME = pathlib.Path.home()
TARGET_DIR = HOME / ".claude" / "statusline"
TARGET_SCRIPT = TARGET_DIR / "usage.py"
SOURCE_SCRIPT = pathlib.Path(__file__).resolve().parent / "usage.py"
STATUS_LINE = {"type": "command", "command": "python3 -I ~/.claude/statusline/usage.py", "padding": 0}


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def backup(path: pathlib.Path, dry_run: bool) -> str:
    copy = path.with_name(f"{path.name}.bak-usage-line-{stamp()}")
    if not dry_run:
        shutil.copy2(path, copy)
    return str(copy)


def install_script(dry_run: bool) -> str:
    new_text = SOURCE_SCRIPT.read_text()
    if TARGET_SCRIPT.exists() and TARGET_SCRIPT.read_text() == new_text:
        return f"script: already current at {TARGET_SCRIPT}"
    note = ""
    if TARGET_SCRIPT.exists():
        note = f" (old copy kept at {backup(TARGET_SCRIPT, dry_run)})"
    if not dry_run:
        TARGET_DIR.mkdir(parents=True, exist_ok=True)
        TARGET_SCRIPT.write_text(new_text)
        TARGET_SCRIPT.chmod(0o755)
    return f"script: installed at {TARGET_SCRIPT}{note}"


def read_settings(settings_path: pathlib.Path):
    """(settings, problem): the parsed object, or None and why it can't be used."""
    if not settings_path.exists():
        return {}, None
    try:
        settings = json.loads(settings_path.read_text() or "{}")
    except ValueError:
        return None, "not valid JSON"
    if not isinstance(settings, dict):
        return None, "not a JSON object"
    return settings, None


def write_settings(settings_path: pathlib.Path, settings: dict) -> None:
    """Replace settings.json in one step, keeping its permissions (people lock it to 600 when it
    holds keys under "env"). A symlinked settings.json (a dotfiles setup) is written through, so
    the link survives."""
    real_path = settings_path.resolve()
    real_path.parent.mkdir(parents=True, exist_ok=True)
    temp = real_path.with_name(real_path.name + ".tmp-usage-line")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(json.dumps(settings, indent=2) + "\n")
    if real_path.exists():
        shutil.copymode(real_path, temp)
    temp.replace(real_path)


def install_settings(config_dir: pathlib.Path, replace: bool, dry_run: bool) -> tuple[bool, str]:
    settings_path = config_dir / "settings.json"
    settings, problem = read_settings(settings_path)
    if problem:
        return False, f"{settings_path}: {problem}, left alone. Fix it, then run this again."
    current = settings.get("statusLine")
    if current == STATUS_LINE:
        return True, f"{settings_path}: already set up"
    if current and not replace:
        return False, (f"{settings_path}: already has a different status line ({json.dumps(current)}). "
                       "Run again with --replace to swap it; a backup is kept.")
    note = f" (backup {backup(settings_path.resolve(), dry_run)})" if settings_path.exists() else ""
    if not dry_run:
        write_settings(settings_path, {**settings, "statusLine": STATUS_LINE})
    return True, f"{settings_path}: status line set{note}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Install the Claude usage status line.")
    parser.add_argument("--config-dir", nargs="*", default=[str(HOME / ".claude")],
                        help="Claude Code config folders to set up (default ~/.claude)")
    parser.add_argument("--replace", action="store_true", help="replace an existing different status line")
    parser.add_argument("--dry-run", action="store_true", help="report what would change without changing it")
    args = parser.parse_args(argv)

    print(("[dry run] " if args.dry_run else "") + install_script(args.dry_run))
    all_ok = True
    for raw in args.config_dir:
        ok, message = install_settings(pathlib.Path(raw).expanduser(), args.replace, args.dry_run)
        all_ok = all_ok and ok
        print(("[dry run] " if args.dry_run else "") + message)
    if all_ok and not args.dry_run:
        print("Done. The line appears in each Claude Code window after its next reply.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
