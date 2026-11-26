#!/usr/bin/env python3
"""Claude Code status line: usage left on every Claude account you use, so you know where to switch.

Example line:
    Work: 5h 62% left (resets 3:40pm), week 48% left  |  Personal: 5h 100%, week 12% (2h ago)

How it works:
- Claude Code passes this script the session's usage for its own account: the 5-hour
  and weekly windows, as percent used, plus reset times.
- The script works out which account the session is on and saves that account's numbers
  under ~/.claude/statusline/usage/. It asks `claude auth status --json` in the background,
  at most every 2 minutes, and caches the answer.
- A window that has been idle redraws its old numbers. A saved record is replaced when this
  window's own numbers changed since it last drew (a new reply, including after a limit reset),
  or when they are plainly newer. The age shown is the last time the numbers moved.
- It shows this account first, then every other account's last-seen numbers and their age.
  Another account's numbers update whenever any session logged into it runs.

Optional settings in ~/.claude/statusline/accounts.json (see references/accounts.example.json):
- "rules": short labels and a cmux workspace color, matched on the account's org name.
- "<orgId>": {"label": "..."}: a label for one exact account.
- "anchors": one known weekly reset per account, which fixes the label right after a /login
  (a running window keeps its old account's numbers until its next reply).

Standard library only. It never prints anything but the status line, and never reads tokens.
"""
import datetime
import fcntl
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

HOME = pathlib.Path.home()
BASE = HOME / ".claude" / "statusline"
USAGE_DIR = BASE / "usage"
ACCOUNT_MAX_AGE_S = 120
READINGS_MAX_AGE_S = 7 * 86400

SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}")
SAFE_COLOR = re.compile(r"#?[A-Za-z0-9]{1,32}")  # "#10B981" or a name like "Purple"; never a "-flag"
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")

CODEX_ID = "codex"  # Codex's numbers are saved as usage/codex.json and drawn like one more account
# Newest day folders under ~/.codex/sessions to look through. A resumed session keeps writing to the
# folder of the day it began, so a week is too short for anyone who resumes old sessions.
CODEX_DAYS_SEARCHED = 31
CODEX_LOGS_TRIED = 5  # newest logs tried for a usage record before giving up
CODEX_TAIL_BYTES = 512 * 1024  # logs reach several MB; the latest usage record is near the end
FIVE_HOUR_MINUTES, WEEK_MINUTES = 300, 10080

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")  # many sessions write at once
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def safe_id(value):
    """The id if it is safe to use as a file name, else None. Session and org ids come from Claude
    Code and accounts.json, but they still name files, so a path can never slip through."""
    text = "" if value is None else str(value)
    return text if SAFE_ID.fullmatch(text) and text not in (".", "..") else None


def clean(text):
    """Drop terminal control characters, so an org name can't move the cursor or retitle the window."""
    return CONTROL_CHARS.sub("", str(text or ""))


def account_cache_path():
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", str(HOME / ".claude"))
    safe = config_dir.strip("/").replace("/", "_")
    return BASE / f"account-{safe}.json"


def claude_binary():
    native = HOME / ".local" / "bin" / "claude"
    return str(native) if native.exists() else (shutil.which("claude") or "claude")


def account_rule(org_name):
    """The first accounts.json rule whose "all" parts all appear in the org name. Matching on the
    name means a rule works for an account this laptop hasn't seen yet."""
    config = read_json(BASE / "accounts.json") or {}
    for rule in config.get("rules", []):
        if all(part in (org_name or "") for part in rule.get("all", [])):
            return rule
    return {}


def set_cmux_color(org_name):
    """Color this cmux workspace to match the account, only when it changed."""
    workspace = os.environ.get("CMUX_WORKSPACE_ID")
    wanted = account_rule(org_name).get("cmux_color")
    if not workspace or not isinstance(wanted, str) or not SAFE_COLOR.fullmatch(wanted):
        return
    marker = BASE / "cmux" / f"{workspace.replace('/', '_')}.txt"
    try:
        if marker.exists() and marker.read_text() == wanted:
            return
    except Exception:
        pass
    cmux = shutil.which("cmux") or "/Applications/cmux.app/Contents/Resources/bin/cmux"
    # Mark it tried before calling, so a failing cmux costs one attempt, not one on every redraw.
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(wanted)
    subprocess.run([cmux, "workspace-action", "--action", "set-color", "--color", wanted,
                    "--workspace", workspace], capture_output=True, text=True, timeout=3)


def refresh_account(cache_path):
    """Runs in a detached child: ask Claude Code which account this config dir is on."""
    try:
        out = subprocess.run([claude_binary(), "auth", "status", "--json"], capture_output=True,
                             text=True, timeout=20).stdout
        status = json.loads(out)
        if status.get("loggedIn") and status.get("orgId"):
            write_json(cache_path, {"orgId": status["orgId"], "orgName": status.get("orgName", ""),
                                    "at": time.time()})
    except Exception:
        pass


def current_account():
    cache_path = account_cache_path()
    cached = read_json(cache_path)
    if not cached or time.time() - cached.get("at", 0) > ACCOUNT_MAX_AGE_S:
        # Stamp the attempt first: when `claude auth status` fails (logged out, API key, no claude
        # on PATH), this keeps it to one try every 2 minutes instead of one on every redraw.
        write_json(cache_path, {**(cached or {}), "at": time.time()})
        try:
            subprocess.Popen([sys.executable, "-I", __file__, "--refresh-account", str(cache_path)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception:
            pass
    return cached


def codex_home():
    return pathlib.Path(os.environ.get("CODEX_HOME") or HOME / ".codex")


def newest_codex_logs():
    """Codex session logs from the newest day folders, most recently written first. Logs live in
    sessions/YYYY/MM/DD/, and a resumed session keeps writing to the folder of the day it began,
    so the newest file by name is not always the newest by use."""
    def newest_first(folder):
        try:
            return sorted(folder.iterdir(), key=lambda path: path.name, reverse=True)
        except OSError:
            return []
    day_folders = [day for year in newest_first(codex_home() / "sessions")
                   for month in newest_first(year) for day in newest_first(month)][:CODEX_DAYS_SEARCHED]
    logs = []
    for day in day_folders:
        for log in newest_first(day):
            try:
                if log.suffix == ".jsonl" and log.is_file():
                    logs.append((log.stat().st_mtime, log))
            except OSError:
                pass
    return [log for _, log in sorted(logs, reverse=True)]


def last_codex_limits(log):
    """(timestamp, rate_limits) from the last usage record in one Codex log, or None. Only the end of
    the file is read, only lines that mention rate_limits are parsed, and only numbers are kept."""
    try:
        with open(log, "rb") as handle:
            handle.seek(max(0, log.stat().st_size - CODEX_TAIL_BYTES))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if '"rate_limits"' not in line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            continue  # the first line of a tail is usually cut off; a freak line can nest too deep
        payload = record.get("payload") if isinstance(record, dict) else None
        limits = payload.get("rate_limits") if isinstance(payload, dict) else None
        if isinstance(limits, dict) and any(codex_windows(limits)):
            return record.get("timestamp"), limits
    return None


def codex_windows(limits):
    """(five_hour, weekly) windows from Codex's rate_limits, matched by length rather than by
    "primary"/"secondary", since the order is Codex's to change."""
    by_length = {}
    for window in (limits.get("primary"), limits.get("secondary")):
        if isinstance(window, dict) and finite_number(window.get("window_minutes")) is not None \
                and finite_number(window.get("used_percent")) is not None:
            by_length[int(window["window_minutes"])] = window
    return by_length.get(FIVE_HOUR_MINUTES), by_length.get(WEEK_MINUTES)


def finite_number(value):
    """The value when it is a real, finite number, else None. Python's json reads NaN and Infinity,
    and a saved NaN would break every redraw until Codex wrote again."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:  # an int too big for a float, such as 10**400
        return None


def epoch_from_iso(text):
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def refresh_codex():
    """Copy Codex's latest usage into usage/codex.json, so the line draws it like another account.
    The newest log's path and write time are remembered, so an idle redraw reads nothing."""
    logs = newest_codex_logs()
    if not logs:
        return
    marker = BASE / "codex-source.json"
    source = [str(logs[0]), logs[0].stat().st_mtime]
    if read_json(marker) == source:
        return
    write_json(marker, source)
    for log in logs[:CODEX_LOGS_TRIED]:
        found = last_codex_limits(log)
        if found:
            break
    else:
        return
    timestamp, limits = found
    five, week = codex_windows(limits)
    write_json(USAGE_DIR / f"{CODEX_ID}.json", {
        "orgName": "Codex",
        "five_used": finite_number((five or {}).get("used_percent")),
        "five_resets": finite_number((five or {}).get("resets_at")),
        "week_used": finite_number((week or {}).get("used_percent")),
        "week_resets": finite_number((week or {}).get("resets_at")),
        # Never later than now: a Codex clock that runs ahead would otherwise freeze the age at 0m.
        "at": min(epoch_from_iso(timestamp) or log.stat().st_mtime, time.time())})


WEEK_S = 7 * 86400


def same_time_of_week(first, second):
    """True when two weekly reset times fall at the same point in the week (within 30 min)."""
    if not first or not second:
        return False
    gap = abs(int(first) - int(second)) % WEEK_S
    return min(gap, WEEK_S - gap) < 1800


def route_by_anchor(org_id, org_name, week_resets, config):
    """Which account these numbers belong to. Each account's weekly limit resets at its own
    fixed time of week, kept in accounts.json "anchors" (orgId -> one known reset). When the
    numbers match exactly one anchor, that account wins over the folder's saved login.

    Why fixed anchors: a first version learned reset times from the saved records as it went.
    One record was already wrong, so it locked a swap in (one account's numbers showed under
    the other's name). Anchors are set by hand from a session known to be on that account."""
    anchors = ((config or {}).get("anchors") or {})
    matches = [anchor_id for anchor_id, info in anchors.items()
               if isinstance(info, dict) and same_time_of_week(info.get("week_reset"), week_resets)]
    if len(matches) != 1 or matches[0] == org_id:
        return org_id, org_name
    return matches[0], anchors[matches[0]].get("orgName", "")


def is_newer(saved, five, week):
    """True when these numbers are newer than the saved ones, judged by the numbers alone: a
    later reset, or a higher percent in the same window."""
    if not saved:
        return True
    pairs = ((week.get("resets_at"), saved.get("week_resets"), week.get("used_percentage"), saved.get("week_used")),
             (five.get("resets_at"), saved.get("five_resets"), five.get("used_percentage"), saved.get("five_used")))
    for new_reset, old_reset, new_used, old_used in pairs:
        if abs((new_reset or 0) - (old_reset or 0)) >= 1800:
            return (new_reset or 0) > (old_reset or 0)
        if (new_used or 0) != (old_used or 0):
            return (new_used or 0) > (old_used or 0)
    return False


def reading_state(session_id, five, week):
    """"first", "same" or "changed": how this window's numbers compare with the last time it drew.
    An idle window redraws the same numbers, so only a change means a new reply, even when the
    percent went DOWN (after a limit reset the bar once stayed at 75% while the real number was
    59%). One small file per session, so windows never overwrite each other's."""
    session_id = safe_id(session_id)
    if not session_id:
        return "first"
    path = BASE / "readings" / f"{session_id}.json"
    previous = read_json(path)
    reading = [week.get("used_percentage"), week.get("resets_at"),
               five.get("used_percentage"), five.get("resets_at")]
    if previous == reading:
        return "same"
    write_json(path, reading)
    if previous is None:
        prune_readings()
        return "first"
    week_moved = reading[0] is not None and reading[:2] != previous[:2]
    five_moved = reading[2] is not None and reading[2:] != previous[2:]
    return "changed" if week_moved or five_moved else "same"  # a limit that just ran out of time drops off


def prune_readings():
    """Each session leaves one small reading file. Drop the ones idle for a week. Runs once per new
    session, so the folder never grows without end."""
    cutoff = time.time() - READINGS_MAX_AGE_S
    for path in (BASE / "readings").glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def note_session(session_id, org_id, label, now):
    """Remember which account each session is really on (sessions.json), for checking by hand.
    Many windows share the file, so the read and write happen under a lock; a window that finds
    it locked skips this draw and notes itself on the next one."""
    session_id = safe_id(session_id)
    if not session_id:
        return
    BASE.mkdir(parents=True, exist_ok=True)
    with open(BASE / "sessions.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        path = BASE / "sessions.json"
        sessions = read_json(path) or {}
        seen = sessions.get(session_id) or {}
        if seen.get("org") == org_id and now - seen.get("at", 0) < 60:
            return
        sessions = {sid: info for sid, info in sessions.items()
                    if isinstance(info, dict) and now - info.get("at", 0) < 86400}
        sessions[session_id] = {"org": org_id, "label": label, "at": now}
        write_json(path, sessions)


def label_for(org_id, org_name, labels):
    if org_id == CODEX_ID:
        # Never run Codex through the Claude rules: a catch-all rule would call it "Org".
        return clean(((labels or {}).get(CODEX_ID) or {}).get("label") or "Codex")
    custom = (labels or {}).get(org_id, {}).get("label") or account_rule(org_name).get("label")
    if custom:
        return clean(custom)
    name = clean(org_name).replace("'s Organization", "")
    return name.split("@")[0] or f"acct {org_id[:4]}"


def color(left):
    return GREEN if left >= 50 else YELLOW if left >= 20 else RED


def window_text(name, used, resets_at, now):
    if used is None:
        return f"{name} ?"
    if resets_at and resets_at < now:
        return f"{name} {GREEN}100%{RESET}"
    left = max(0, round(100 - used))
    return f"{name} {color(left)}{left}%{RESET}"


def reset_clock(resets_at):
    if not resets_at:
        return ""
    return time.strftime("%-I:%M%p", time.localtime(resets_at)).lower()


def age_text(seconds):
    seconds = max(0, seconds)  # a clock that runs ahead must not print "-205m ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 2 * 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def main():
    os.umask(0o077)  # saved files hold org names, which can include an email: owner-only
    if len(sys.argv) == 3 and sys.argv[1] == "--refresh-account":
        refresh_account(pathlib.Path(sys.argv[2]))
        return
    try:
        draw()
    except Exception:
        # A status line must never go blank or dump a traceback, even with a broken accounts.json.
        print("usage: unavailable (see the usage-line skill's troubleshooting)")


def draw():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    now = time.time()
    limits = data.get("rate_limits") or {}
    five = limits.get("five_hour") or {}
    week = limits.get("seven_day") or {}
    account = current_account()
    labels = read_json(BASE / "accounts.json") or {}
    org_id = (account or {}).get("orgId")
    org_name = (account or {}).get("orgName", "")

    # After a /login, the folder names the new account while this window still holds the old
    # account's numbers (until its next reply). So check the numbers' weekly reset time, which
    # is fixed per account, before trusting the folder.
    if org_id and week.get("resets_at"):
        org_id, org_name = route_by_anchor(org_id, org_name, week.get("resets_at"), labels)
    org_id = safe_id(org_id)

    if org_id and (five or week):
        saved = read_json(USAGE_DIR / f"{org_id}.json")
        state = reading_state(data.get("session_id"), five, week)
        if state == "changed" or not saved or (state == "first" and is_newer(saved, five, week)):
            write_json(USAGE_DIR / f"{org_id}.json", {
                "orgName": org_name,
                "five_used": five.get("used_percentage"), "five_resets": five.get("resets_at"),
                "week_used": week.get("used_percentage"), "week_resets": week.get("resets_at"),
                "at": now})
        try:
            note_session(data.get("session_id"), org_id, label_for(org_id, org_name, labels), now)
            set_cmux_color(org_name)
        except Exception:
            pass

    try:
        refresh_codex()
    except Exception:
        pass  # a change in Codex's log format must never take the Claude numbers down with it

    parts = []
    showing_numbers = False
    if org_id:
        here = label_for(org_id, org_name, labels)
        if five or week:
            showing_numbers = True
            five_text = (f"{window_text('5h', five.get('used_percentage'), five.get('resets_at'), now)}"
                         f" (resets {reset_clock(five.get('resets_at'))}), " if five else "")
            text = (f"{here}: {five_text}"
                    f"{window_text('week', week.get('used_percentage'), week.get('resets_at'), now)}")
        else:
            text = f"{here}: waiting for first reply"
        parts.append(text)

    saved_accounts = sorted(USAGE_DIR.glob("*.json")) if USAGE_DIR.exists() else []
    for path in sorted(saved_accounts, key=lambda path: path.stem == CODEX_ID):  # Codex last
        other_id = path.stem
        if other_id == org_id:
            continue
        other = read_json(path) or {}
        showing_numbers = True
        age = now - other.get("at", 0)
        name = label_for(other_id, other.get("orgName"), labels)
        five_text = (f"{window_text('5h', other.get('five_used'), other.get('five_resets'), now)}, "
                     if other.get("five_used") is not None else "")
        parts.append(f"{name}: {five_text}{window_text('week', other.get('week_used'), other.get('week_resets'), now)}"
                     f" {DIM}({age_text(age)}){RESET}")

    # "left" is said once, up front, so every percent reads the same way: a bare "5h 0%" on
    # another account was easy to misread as 0% used.
    prefix = f"{DIM}Usage left ·{RESET} " if showing_numbers else ""
    print(prefix + "  |  ".join(parts) if parts else "usage: checking account")


if __name__ == "__main__":
    main()
