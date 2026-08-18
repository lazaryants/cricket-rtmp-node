import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_runtime_paths_are_inside_project_root_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"CRICKET_RTMP_ROOT": directory},
                clear=True,
            ):
                settings = Settings()
                root = Path(directory)
                self.assertEqual(settings.config_file, root / "state/restream-config.json")
                self.assertEqual(settings.run_dir, root / "run")
                self.assertEqual(settings.log_dir, root / "logs")
                self.assertEqual(settings.template_dir, root / "app/templates")

    def test_explicit_overrides_are_respected(self):
        environment = {
            "CRICKET_RTMP_ROOT": "/srv/node",
            "CRICKET_RTMP_CONFIG": "/etc/node.json",
            "CRICKET_RTMP_RUN_DIR": "/run/node",
            "CRICKET_RTMP_LOG_DIR": "/var/log/node",
            "CRICKET_RTMP_PUBLIC_HOST": "ingest.example.test",
            "CRICKET_RTMP_LOCAL_HLS_ORIGIN": "http://127.0.0.1:18081/hls/",
            "CRICKET_RTMP_LOCAL_RTMP_ORIGIN": "rtmp://127.0.0.1:19350/",
            "CRICKET_RTMP_AUTH_BIND": "127.0.0.1:18080",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings()
            self.assertEqual(settings.config_file, Path("/etc/node.json"))
            self.assertEqual(settings.run_dir, Path("/run/node"))
            self.assertEqual(settings.log_dir, Path("/var/log/node"))
            self.assertEqual(settings.public_host, "ingest.example.test")
            self.assertEqual(
                settings.local_hls_origin,
                "http://127.0.0.1:18081/hls",
            )
            self.assertEqual(
                settings.local_rtmp_origin,
                "rtmp://127.0.0.1:19350",
            )
            self.assertEqual(settings.auth_address, ("127.0.0.1", 18080))

    def test_auth_bind_defaults_to_loopback_port_8080(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
            self.assertEqual(settings.auth_address, ("127.0.0.1", 8080))

    def test_auth_bind_rejects_invalid_values(self):
        for value in (
            "127.0.0.1",
            ":8080",
            "127.0.0.1:not-a-port",
            "127.0.0.1:0",
            "127.0.0.1:65536",
        ):
            with self.subTest(value=value):
                with patch.dict(
                    os.environ,
                    {"CRICKET_RTMP_AUTH_BIND": value},
                    clear=True,
                ):
                    with self.assertRaises(ValueError):
                        Settings().auth_address


if __name__ == "__main__":
    unittest.main()
