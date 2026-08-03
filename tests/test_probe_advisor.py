import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
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


class NotificationChannelTests(unittest.TestCase):
    def test_list_notification_channels_follows_pages(self):
        pages = [
            {"notificationChannels": [{"name": "channels/1"}], "nextPageToken": "next token"},
            {"notificationChannels": [{"name": "channels/2"}]},
        ]
        with mock.patch.object(probe, "_get_json", side_effect=pages) as get_json:
            channels = probe._list_notification_channels("example-project", "token")

        self.assertEqual([channel["name"] for channel in channels], ["channels/1", "channels/2"])
        self.assertEqual(get_json.call_count, 2)
        self.assertIn("pageToken=next+token", get_json.call_args_list[1].args[0])

    def test_command_gets_token_and_prints_channels(self):
        channel = {
            "name": "projects/example/notificationChannels/123",
            "displayName": "Example",
            "type": "pubsub",
            "enabled": True,
        }
        with (
            mock.patch.object(probe, "_preflight_gcloud_auth", return_value=("token", "user@example.com")) as auth,
            mock.patch.object(probe, "_list_notification_channels", return_value=[channel]) as list_channels,
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            probe.cmd_notification_channels(SimpleNamespace(project="example"))

        auth.assert_called_once_with()
        list_channels.assert_called_once_with("example", "token")
        self.assertIn("projects/example/notificationChannels/123", stdout.getvalue())
        self.assertIn("Example", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()