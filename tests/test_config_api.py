import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import restream_manager
from app.config_store import ConfigStore


class ConfigApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "restream-config.json"
        self.store = ConfigStore(self.path)
        self.store.save({
            "schema_version": 1,
            "fields": {
                "1": {
                    "name": "Place 1",
                    "enabled": True,
                    "stream_key": "stream1",
                    "key": "publish-key",
                    "publish_auth_enabled": True,
                    "restream_urls": [],
                },
            },
        })
        self.store_patch = patch.object(restream_manager, "CONFIG_STORE", self.store)
        self.store_patch.start()
        self.client = restream_manager.app.test_client()

    def tearDown(self):
        self.store_patch.stop()
        self.temporary_directory.cleanup()

    def test_rejects_invalid_destination_without_changing_config(self):
        original = self.path.read_bytes()
        response = self.client.post(
            "/api/restream-urls/1",
            json={"url": "https://not-an-rtmp-destination.example"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.path.read_bytes(), original)

    def test_valid_destination_is_persisted(self):
        response = self.client.post(
            "/api/restream-urls/1",
            json={"url": "rtmps://destination.example/live/token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.store.load()["fields"]["1"]["restream_urls"],
            ["rtmps://destination.example/live/token"],
        )

    def test_log_tail_is_bounded_and_returns_complete_last_lines(self):
        log_path = Path(self.temporary_directory.name) / "large.log"
        log_path.write_text(
            ("old-line\n" * 30000)
            + "recent-one\n"
            + "recent-two\n",
            encoding="utf-8",
        )

        lines = restream_manager.read_log_tail(
            log_path,
            line_count=2,
            max_bytes=1024,
        )

        self.assertEqual(lines, ["recent-one", "recent-two"])

    @patch.object(restream_manager, "get_process_status")
    def test_status_snapshot_is_dynamic_and_does_not_expose_urls(
        self,
        mocked_status,
    ):
        self.store.save({
            "schema_version": 1,
            "fields": {
                "1": {
                    "name": "Place 1",
                    "enabled": True,
                    "stream_key": "stream1",
                    "key": "publish-key",
                    "publish_auth_enabled": True,
                    "restream_urls": [
                        "rtmp://destination.example/live/private-token",
                    ],
                },
            },
        })
        mocked_status.return_value = {
            "status": "running",
            "pid": 123,
            "uptime": 42,
            "cpu": 1.5,
            "memory": 24.0,
        }

        response = self.client.get("/api/status")
        payload = response.get_json()
        serialized = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["fields"]["1"]["running_count"], 1)
        self.assertEqual(
            payload["fields"]["1"]["destinations"][0]["status"],
            "running",
        )
        self.assertNotIn("private-token", serialized)
        self.assertNotIn("publish-key", serialized)


if __name__ == "__main__":
    unittest.main()
