from pathlib import Path
import argparse
import tempfile
import unittest
from unittest import mock

import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("probe", ROOT / "scripts" / "probe.py")
probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(probe)


class ProbeVerifyContractTests(unittest.TestCase):
    def test_probe_refresh_runs_only_on_first_conversation_turn(self):
        skill = (ROOT / "skills/probe/SKILL.md").read_text()
        normalized_skill = " ".join(skill.split())

        self.assertIn("Run only on the first turn of this `/probe` conversation", normalized_skill)
        self.assertIn("skip this section on later messages", normalized_skill)

    def test_prompt_requires_change_number(self):
        skill = (ROOT / "skills/probe-verify/SKILL.md").read_text()
        command = (ROOT / "commands/probe-verify.toml").read_text()

        self.assertIn("Ask the user for the exact ServiceNow change number", skill)
        self.assertIn("probe.py advisor --chg <CHG>", skill)
        self.assertIn("ask the user for the exact", command.lower())
        self.assertNotIn("armada", (skill + command).lower())
        self.assertNotIn("Playwright", skill + command)

    def test_first_refresh_without_state_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry.yaml"
            registry.write_text("refresh_interval_days: 30\n")
            args = argparse.Namespace(
                registry=str(registry),
                state=str(Path(directory) / "missing.json"),
            )
            with mock.patch("builtins.print") as output:
                with self.assertRaises(SystemExit) as raised:
                    probe.cmd_check_refresh(args)

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("never refreshed", " ".join(str(call) for call in output.call_args_list))


if __name__ == "__main__":
    unittest.main()