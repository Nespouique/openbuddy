import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

from pc import quota

COPILOT = {"credits_used": 0, "entitlement": 200, "has_quota": True, "percent_remaining": 100,
           "quota_id": "premium_interactions", "unlimited": False}


def write_config(root, **changes):
    provider = {"command": ["gh", "api", "copilot_internal/user", "--jq", ".quota_snapshots.premium_interactions"],
                "used": "credits_used", "limit": "entitlement"}
    data = {"provider": "github-copilot", "refresh_seconds": 300,
            "providers": {"github-copilot": dict(provider, **changes)}}
    path = Path(root) / "quota.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class QuotaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name

    def test_copilot_snapshot_is_displayed_as_used_over_limit(self):
        config = quota.load_config(write_config(self.root))
        self.assertEqual(quota.format_quota(json.dumps(COPILOT), config), "0 / 200")
        self.assertEqual(quota.format_quota(json.dumps(dict(COPILOT, credits_used=37.5)), config), "37.5 / 200")
        self.assertEqual(quota.format_quota(json.dumps(dict(COPILOT, credits_used=12.0)), config), "12 / 200")

    def test_label_is_optional_prefix(self):
        config = quota.load_config(write_config(self.root, label="Copilot"))
        self.assertEqual(quota.format_quota(json.dumps(COPILOT), config), "Copilot 0 / 200")

    def test_command_string_is_split_without_a_shell(self):
        command = 'gh api copilot_internal/user --jq ".quota_snapshots.premium_interactions"'
        config = quota.load_config(write_config(self.root, command=command))
        self.assertEqual(config.command[-1], ".quota_snapshots.premium_interactions")

    def test_bad_output_and_configuration_are_rejected(self):
        config = quota.load_config(write_config(self.root))
        for output in ('{"credits_used": 0}', '{"credits_used": "0", "entitlement": 200}', "[]", "not json"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                quota.format_quota(output, config)
        for changes in ({"command": []}, {"command": "  "}, {"command": [1]}, {"used": 3}, {"label": 4}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                quota.load_config(write_config(self.root, **changes))
        path = write_config(self.root)
        data = json.loads(path.read_text())
        data["refresh_seconds"] = 5
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            quota.load_config(path)
        data.update(refresh_seconds=300, provider="missing")
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            quota.load_config(path)

    def test_text_longer_than_the_protocol_limit_is_rejected(self):
        config = quota.load_config(write_config(self.root, label="x" * 30))
        with self.assertRaises(ValueError):
            quota.format_quota(json.dumps(COPILOT), config)

    def test_fetch_runs_the_command_and_reports_failures(self):
        script = "import json; print(json.dumps({'credits_used': 3, 'entitlement': 50}))"
        config = quota.load_config(write_config(self.root, command=[sys.executable, "-c", script]))
        self.assertEqual(quota.fetch(config), "3 / 50")
        failing = quota.load_config(write_config(self.root, command=[sys.executable, "-c", "raise SystemExit(3)"]))
        with self.assertRaises(ValueError):
            quota.fetch(failing)
        missing = quota.load_config(write_config(self.root, command=["openbuddy-no-such-command"]))
        with self.assertRaises(ValueError):
            quota.fetch(missing)

    def test_monitor_keeps_the_last_good_text_when_the_command_fails(self):
        async def scenario():
            path = write_config(self.root)
            results = iter(["1 / 200", ValueError("offline")])

            def fetcher(_config):
                value = next(results)
                if isinstance(value, Exception):
                    raise value
                return value

            monitor = quota.QuotaMonitor(path, fetcher)
            task = asyncio.create_task(monitor.run())
            for _ in range(50):
                await asyncio.sleep(0.01)
                if monitor.text:
                    break
            self.assertEqual(monitor.text, "1 / 200")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())

    def test_missing_configuration_is_silent_and_disables_the_display(self):
        async def scenario():
            monitor = quota.QuotaMonitor(Path(self.root) / "absent.json", lambda _config: "never")
            task = asyncio.create_task(monitor.run())
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual((monitor.text, monitor.error), ("", None))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
