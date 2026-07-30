import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("probe", ROOT / "scripts" / "probe.py")
probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(probe)


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode() if isinstance(payload, dict) else payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class AdvisorTests(unittest.TestCase):
    def test_get_advisor_json(self):
        payload = {"change": "CHG1", "error": "failed", "advice": "fix it"}
        with mock.patch.object(probe.urllib.request, "urlopen", return_value=FakeResponse(payload)) as urlopen:
            self.assertEqual(probe._get_advisor_json("CHG1"), payload)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f"{probe.GCP_ADVISOR_BASE_URL}/advisor/CHG1")
        self.assertEqual(request.get_header("Accept"), "application/json")

    def test_change_number_is_url_encoded(self):
        with mock.patch.object(probe.urllib.request, "urlopen", return_value=FakeResponse({})) as urlopen:
            probe._get_advisor_json("CHG/1")

        self.assertTrue(urlopen.call_args.args[0].full_url.endswith("/advisor/CHG%2F1"))

    def test_404_reports_bad_change_number(self):
        error = urllib.error.HTTPError("url", 404, "missing", {}, io.BytesIO(b""))
        with mock.patch.object(probe.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(probe.ApiUsageIssue, "Double-check the change number"):
                probe._get_advisor_json("CHG0")

    def test_network_error_is_environment_issue(self):
        error = urllib.error.URLError("offline")
        with mock.patch.object(probe.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(probe.EnvironmentIssue):
                probe._get_advisor_json("CHG1")

    def test_invalid_json_is_api_issue(self):
        with mock.patch.object(probe.urllib.request, "urlopen", return_value=FakeResponse(b"not json")):
            with self.assertRaisesRegex(probe.ApiUsageIssue, "invalid JSON"):
                probe._get_advisor_json("CHG1")


if __name__ == "__main__":
    unittest.main()