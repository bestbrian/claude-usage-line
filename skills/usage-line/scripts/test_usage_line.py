"""Tests for the usage status line and its installer.

Each test runs the scripts in a child process with HOME pointed at a temp folder, so nothing
touches the real ~/.claude. Run: python3 -m unittest test_usage_line   (from this folder)
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ANSI = re.compile(r"\033\[[0-9;]*m")
NOW = int(time.time())


def run(script: str, home: pathlib.Path, args=(), stdin: str = "", extra_env=None):
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", **(extra_env or {})}
    done = subprocess.run([sys.executable, "-I", str(HERE / script), *args], input=stdin,
                          capture_output=True, text=True, env=env, timeout=30)
    return done.returncode, ANSI.sub("", done.stdout), done.stderr


def seed_account(home: pathlib.Path, config_dir: pathlib.Path, org_id: str, org_name: str):
    """What the background `claude auth status` refresh would have cached, so tests never call claude."""
    safe = str(config_dir).strip("/").replace("/", "_")
    path = home / ".claude" / "statusline" / f"account-{safe}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"orgId": org_id, "orgName": org_name, "at": time.time()}))


def status_input(session_id: str, five_used: float, week_used: float, week_reset: int = NOW + 3 * 86400):
    return json.dumps({"session_id": session_id, "rate_limits": {
        "five_hour": {"used_percentage": five_used, "resets_at": NOW + 3600},
        "seven_day": {"used_percentage": week_used, "resets_at": week_reset}}})


class StatusLineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.temp.name)
        self.config = self.home / ".claude"
        self.env = {"CLAUDE_CONFIG_DIR": str(self.config)}

    def tearDown(self):
        self.temp.cleanup()

    def test_shows_this_accounts_usage_left(self):
        seed_account(self.home, self.config, "org-work", "Acme's Organization")
        code, out, _ = run("usage.py", self.home, stdin=status_input("s1", 38, 52), extra_env=self.env)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Usage left · Acme: 5h 62% (resets "), out)
        self.assertIn("week 48%", out)
        self.assertNotIn("% left", out)

    def test_other_accounts_show_with_their_age(self):
        seed_account(self.home, self.config, "org-work", "Acme's Organization")
        run("usage.py", self.home, stdin=status_input("s1", 10, 20), extra_env=self.env)
        seed_account(self.home, self.config, "org-home", "someone@gmail.com's Organization")
        _, out, _ = run("usage.py", self.home, stdin=status_input("s2", 90, 95, NOW + 5 * 86400),
                        extra_env=self.env)
        first, second = out.strip().split("  |  ")
        self.assertTrue(first.startswith("Usage left · someone: 5h 10% (resets "), first)
        self.assertIn("Acme: 5h 90%, week 80% (0m ago)", second)

    def test_labels_from_accounts_json(self):
        seed_account(self.home, self.config, "org-work", "Acme's Organization")
        (self.home / ".claude" / "statusline" / "accounts.json").write_text(
            json.dumps({"rules": [{"all": ["Acme"], "label": "Work"}]}))
        _, out, _ = run("usage.py", self.home, stdin=status_input("s1", 0, 0), extra_env=self.env)
        self.assertTrue(out.startswith("Usage left · Work: 5h 100%"), out)

    def test_an_idle_redraw_never_overwrites_newer_numbers(self):
        seed_account(self.home, self.config, "org-work", "Acme's Organization")
        run("usage.py", self.home, stdin=status_input("old-window", 20, 30), extra_env=self.env)
        run("usage.py", self.home, stdin=status_input("new-window", 60, 70), extra_env=self.env)
        run("usage.py", self.home, stdin=status_input("old-window", 20, 30), extra_env=self.env)
        saved = json.loads((self.home / ".claude" / "statusline" / "usage" / "org-work.json").read_text())
        self.assertEqual(saved["five_used"], 60)

    def test_a_drop_after_a_limit_reset_is_saved(self):
        seed_account(self.home, self.config, "org-work", "Acme's Organization")
        run("usage.py", self.home, stdin=status_input("w1", 75, 75), extra_env=self.env)
        run("usage.py", self.home, stdin=status_input("w1", 75, 59), extra_env=self.env)
        saved = json.loads((self.home / ".claude" / "statusline" / "usage" / "org-work.json").read_text())
        self.assertEqual(saved["week_used"], 59)

    def test_anchor_names_the_real_account_after_a_login(self):
        week_reset = NOW + 2 * 86400
        seed_account(self.home, self.config, "org-new", "New's Organization")
        (self.home / ".claude" / "statusline" / "accounts.json").write_text(json.dumps(
            {"anchors": {"org-old": {"week_reset": week_reset - 7 * 86400, "orgName": "Old's Organization"}}}))
        _, out, _ = run("usage.py", self.home, stdin=status_input("s1", 5, 5, week_reset), extra_env=self.env)
        self.assertTrue(out.startswith("Usage left · Old: "), out)

    def test_no_numbers_yet_has_no_usage_left_prefix(self):
        seed_account(self.home, self.config, "org-work", "Acme's Organization")
        _, out, _ = run("usage.py", self.home, stdin=json.dumps({"session_id": "s1"}), extra_env=self.env)
        self.assertEqual(out.strip(), "Acme: waiting for first reply")

    def test_a_session_id_that_is_a_path_writes_nothing_outside(self):
        seed_account(self.home, self.config, "org-work", "Acme's Organization")
        outside = self.home / "outside"
        run("usage.py", self.home, stdin=status_input(str(outside), 10, 10), extra_env=self.env)
        run("usage.py", self.home, stdin=status_input("../../escaped", 10, 10), extra_env=self.env)
        self.assertFalse(pathlib.Path(str(outside) + ".json").exists())
        self.assertFalse((self.home / "escaped.json").exists())

    def test_control_characters_in_an_org_name_never_reach_the_terminal(self):
        seed_account(self.home, self.config, "org-work", "Evil\x1b]0;pwned\x07Co's Organization")
        done = subprocess.run([sys.executable, "-I", str(HERE / "usage.py")], input=status_input("s1", 10, 10),
                              capture_output=True, text=True, timeout=30,
                              env={"HOME": str(self.home), "PATH": "/usr/bin:/bin", **self.env})
        self.assertNotIn("\x07", done.stdout)
        self.assertNotIn("\x1b]", done.stdout)
        self.assertIn("Evil]0;pwnedCo:", ANSI.sub("", done.stdout))

    def test_a_broken_accounts_json_still_prints_a_line(self):
        seed_account(self.home, self.config, "org-work", "Acme's Organization")
        (self.home / ".claude" / "statusline" / "accounts.json").write_text(json.dumps({"rules": "oops"}))
        code, out, _ = run("usage.py", self.home, stdin=status_input("s1", 10, 10), extra_env=self.env)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "usage: unavailable (see the usage-line skill's troubleshooting)")

    def test_saved_files_are_readable_by_their_owner_only(self):
        seed_account(self.home, self.config, "org-work", "someone@gmail.com's Organization")
        run("usage.py", self.home, stdin=status_input("s1", 10, 10), extra_env=self.env)
        saved = self.home / ".claude" / "statusline" / "usage" / "org-work.json"
        self.assertEqual(oct(saved.stat().st_mode & 0o777), oct(0o600))

    def test_a_failed_account_lookup_is_retried_at_most_every_two_minutes(self):
        code, out, _ = run("usage.py", self.home, stdin=status_input("s1", 10, 10), extra_env=self.env)
        self.assertEqual(out.strip(), "usage: checking account")
        cache = list((self.home / ".claude" / "statusline").glob("account-*.json"))
        self.assertEqual(len(cache), 1, "the attempt is stamped, so the next redraw doesn't start claude again")
        self.assertGreater(json.loads(cache[0].read_text())["at"], NOW - 60)

    def test_week_old_readings_are_cleaned_up(self):
        seed_account(self.home, self.config, "org-work", "Acme's Organization")
        readings = self.home / ".claude" / "statusline" / "readings"
        readings.mkdir(parents=True)
        stale, fresh = readings / "old-session.json", readings / "recent-session.json"
        for path in (stale, fresh):
            path.write_text("[]")
        eight_days_ago = time.time() - 8 * 86400
        os.utime(stale, (eight_days_ago, eight_days_ago))
        run("usage.py", self.home, stdin=status_input("brand-new-session", 10, 10), extra_env=self.env)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_bad_input_still_prints_a_line(self):
        code, out, _ = run("usage.py", self.home, stdin="not json", extra_env=self.env)
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.temp.name)
        self.settings = self.home / ".claude" / "settings.json"

    def tearDown(self):
        self.temp.cleanup()

    def read_settings(self):
        return json.loads(self.settings.read_text())

    def test_fresh_install_sets_the_line_and_keeps_other_settings(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({"model": "opus"}))
        code, out, _ = run("install.py", self.home)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read_settings()["model"], "opus")
        self.assertIn("usage.py", self.read_settings()["statusLine"]["command"])
        self.assertTrue((self.home / ".claude" / "statusline" / "usage.py").exists())
        self.assertEqual(len(list(self.settings.parent.glob("settings.json.bak-usage-line-*"))), 1)

    def test_running_twice_changes_nothing(self):
        run("install.py", self.home)
        code, out, _ = run("install.py", self.home)
        self.assertEqual(code, 0)
        self.assertIn("already set up", out)
        self.assertIn("already current", out)

    def test_an_existing_status_line_is_refused_without_replace(self):
        self.settings.parent.mkdir(parents=True)
        mine = {"type": "command", "command": "my-line.sh"}
        self.settings.write_text(json.dumps({"statusLine": mine}))
        code, out, _ = run("install.py", self.home)
        self.assertEqual(code, 1)
        self.assertEqual(self.read_settings()["statusLine"], mine)
        code, _, _ = run("install.py", self.home, args=["--replace"])
        self.assertEqual(code, 0)
        self.assertIn("usage.py", self.read_settings()["statusLine"]["command"])

    def test_dry_run_changes_nothing(self):
        code, out, _ = run("install.py", self.home, args=["--dry-run"])
        self.assertEqual(code, 0)
        self.assertFalse(self.settings.exists())
        self.assertFalse((self.home / ".claude" / "statusline").exists())

    def test_locked_settings_stay_locked(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({"env": {"SOME_KEY": "placeholder"}}))
        self.settings.chmod(0o600)
        code, out, _ = run("install.py", self.home)
        self.assertEqual(code, 0, out)
        self.assertEqual(oct(self.settings.stat().st_mode & 0o777), oct(0o600))

    def test_a_symlinked_settings_file_keeps_its_link(self):
        dotfiles = self.home / "dotfiles"
        dotfiles.mkdir()
        real = dotfiles / "claude-settings.json"
        real.write_text(json.dumps({"model": "opus"}))
        self.settings.parent.mkdir(parents=True)
        self.settings.symlink_to(real)
        code, out, _ = run("install.py", self.home)
        self.assertEqual(code, 0, out)
        self.assertTrue(self.settings.is_symlink())
        self.assertIn("statusLine", json.loads(real.read_text()))

    def test_settings_that_are_not_an_object_are_left_alone(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("[]")
        code, out, _ = run("install.py", self.home)
        self.assertEqual(code, 1)
        self.assertIn("not a JSON object", out)
        self.assertEqual(self.settings.read_text(), "[]")

    def test_purge_keeps_files_it_did_not_make(self):
        run("install.py", self.home)
        folder = self.home / ".claude" / "statusline"
        (folder / "accounts.json").write_text("{}")
        (folder / "my-own-line.sh").write_text("echo hi")
        (folder / "usage").mkdir()
        (folder / "usage" / "org.json").write_text("{}")
        code, out, _ = run("uninstall.py", self.home, args=["--purge"])
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ["accounts.json", "my-own-line.sh"])

    def test_broken_settings_are_left_alone(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{broken")
        code, out, _ = run("install.py", self.home)
        self.assertEqual(code, 1)
        self.assertEqual(self.settings.read_text(), "{broken")

    def test_several_config_folders(self):
        work = self.home / ".claude-work"
        code, _, _ = run("install.py", self.home, args=["--config-dir", str(self.home / ".claude"), str(work)])
        self.assertEqual(code, 0)
        self.assertIn("statusLine", json.loads((work / "settings.json").read_text()))

    def test_uninstall_removes_only_this_line(self):
        run("install.py", self.home)
        code, out, _ = run("uninstall.py", self.home)
        self.assertEqual(code, 0, out)
        self.assertNotIn("statusLine", self.read_settings())
        self.settings.write_text(json.dumps({"statusLine": {"type": "command", "command": "other"}}))
        run("uninstall.py", self.home)
        self.assertIn("statusLine", self.read_settings())


if __name__ == "__main__":
    unittest.main()
