---
name: usage-line
description: Install, label and repair the Claude Code usage status line, which shows the 5-hour and weekly usage left on every Claude account someone uses, so they know when and where to switch. Use when someone asks to set up the usage line or usage viewer, wants to see their Claude limits or usage left in the status line, juggles several Claude accounts, asks to label accounts or color cmux workspaces by account, or says the usage line is wrong or missing.
---

# Usage line

A Claude Code status line that shows the usage left on the account the window is on, then the last-seen numbers for every other account:

```
Usage left · Work: 5h 62% (resets 3:40pm), week 48%  |  Personal: 5h 100%, week 12% (2h ago)
```

Green means 50% or more left, yellow 20 to 49%, red under 20%.

Everything is in this skill's folder:
- `scripts/usage.py` is the status line.
- `scripts/install.py` and `scripts/uninstall.py` set it up and take it down. Both are safe to run twice and back up any file they change.
- `scripts/test_usage_line.py` holds the tests.
- `references/accounts.example.json` shows the optional labels file.

## Before installing

1. Check that `python3 --version` prints 3.9 or newer. The Mac's built-in Python is fine. The script uses only the standard library.
2. The numbers come from Claude Code itself and appear only for Claude subscription logins (Pro, Max, Team, Enterprise). With an API key there is nothing to show. Say so and stop if that is their setup.
3. Ask whether they use more than one Claude account, and how:
   - **One login folder** (`~/.claude`, switching with `/login`): install with no options.
   - **A folder per account** (they start Claude with `CLAUDE_CONFIG_DIR=~/.claude-work claude`, or with wrapper commands): list every folder and pass them all with `--config-dir`.

## Install

From this skill's folder:

```bash
python3 scripts/install.py --dry-run [--config-dir ~/.claude ~/.claude-work]
```

Show the person what it will change, then run it again without `--dry-run`.

If a folder already has a different status line, the installer refuses and leaves it alone. Ask before using `--replace`, because their current status line goes away (a backup is kept next to settings.json).

The line appears in each open window after its next reply. Until then that window's account says "waiting for first reply".

## Labels and colors (optional)

Without a labels file, each account is named after its org, for example `someone@gmail.com's Organization` shows as `someone`.

For short names, write `~/.claude/statusline/accounts.json`. Model it on `references/accounts.example.json`.
- To find each account's org name, run `claude auth status --json` in each login folder (`CLAUDE_CONFIG_DIR=<folder> claude auth status --json`) and read `orgName` and `orgId`. Never print or copy anything else it shows.
- `rules` match on parts of the org name and can set `cmux_color`. Inside cmux, the workspace then turns that color, so each account is easy to tell apart at a glance.
- An `<orgId>` key with a `label` names one exact account.

## Anchors (only if labels swap after a /login)

After a `/login`, a window that is still open keeps reporting its old account's numbers until its next reply, so for a moment the line can name the wrong account. Anchors fix that. Each account's weekly limit resets at the same time every week.
1. In a window known to be on that account, read `~/.claude/statusline/usage/<orgId>.json` and copy its `week_resets` value.
2. Add `"anchors": {"<orgId>": {"week_reset": <that value>, "orgName": "<org name>"}}` to accounts.json.

Only add anchors from a window you are sure of. A wrong anchor locks the swap in.

## When something is wrong

- **The line is missing:** check settings.json has `"statusLine"` with the command `python3 -I ~/.claude/statusline/usage.py`, and that the file exists. Rerun `install.py`.
- **"usage: unavailable":** the script hit an error, almost always a broken `~/.claude/statusline/accounts.json`. Check it is valid JSON shaped like `references/accounts.example.json`.
- **"usage: checking account" stays up:** the script asks `claude auth status --json` in the background. Run that command in the same folder. If it fails or says logged out, run `/login`.
- **Another account's numbers look old:** they update only when a window on that account replies. The age in brackets says how old they are.
- **To test the script by hand:** `echo '{}' | python3 -I ~/.claude/statusline/usage.py` draws the line from the saved numbers without changing them. Never pipe in made-up usage numbers: they would be saved as that account's real ones.
- **To run the tests:** `cd scripts && python3 -m unittest test_usage_line`

## Uninstall

```bash
python3 scripts/uninstall.py [--config-dir ...] [--purge]
```

It removes the status line only where it is this one. `--purge` also deletes this tool's files in `~/.claude/statusline` (the script and the saved numbers). The person's `accounts.json` and any other files in that folder stay.

## What it stores

Only in `~/.claude/statusline/`:
- `usage/<orgId>.json`: each account's last numbers.
- `account-*.json`: which account each login folder is on (org id and name).
- `readings/` and `sessions.json`: small per-window notes. Readings idle for a week are deleted.

Files are created readable by their owner only, since an org name can contain an email address. It never reads tokens and never sends anything anywhere.
