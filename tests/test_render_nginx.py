import json
import tempfile
import unittest
from pathlib import Path

from scripts.render_nginx import render


class RenderNginxTests(unittest.TestCase):
    def profile(self):
        return {
            "server_names": ["rtmp.example.test", "node.example.test"],
            "tls_certificate": "/etc/tls/fullchain.pem",
            "tls_certificate_key": "/etc/tls/privkey.pem",
            "web_root": "/opt/cricket-rtmp-node/web",
            "hls_root": "/var/www/hls",
            "basic_auth_file": "/etc/nginx/.htpasswd",
            "manager_upstream": "127.0.0.1:5000",
            "rtmp_port": 1935,
            "auth_callback": "http://127.0.0.1:8080/auth",
            "auth_places": [15],
        }

    def render_profile(self, profile):
        temporary_directory = tempfile.TemporaryDirectory()
        root = Path(temporary_directory.name)
        profile_path = root / "profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        outputs = render(profile_path, root / "output")
        return temporary_directory, outputs

    def test_renders_all_places_and_selected_auth(self):
        temporary_directory, outputs = self.render_profile(self.profile())
        self.addCleanup(temporary_directory.cleanup)

        rtmp = outputs["cricket-rtmp.conf"]
        http = outputs["cricket-rtmp-http.conf"]
        self.assertEqual(rtmp.count("application place"), 16)
        self.assertEqual(rtmp.count("on_publish "), 1)
        self.assertEqual(rtmp.count("rtmp_auto_push on;"), 1)
        self.assertEqual(rtmp.count("rtmp_auto_push_reconnect 1s;"), 1)
        self.assertLess(rtmp.index("rtmp_auto_push on;"), rtmp.index("rtmp {"))
        self.assertIn("application place15", rtmp)
        self.assertIn("rtmp.example.test node.example.test", http)
        self.assertIn("location ^~ /.well-known/acme-challenge/", http)
        self.assertIn("root /var/lib/letsencrypt;", http)
        self.assertIn("auth_basic off;", http)
        self.assertIn("return 301 https://$host$request_uri;", http)
        self.assertNotIn("@@", rtmp + http)

    def test_rejects_unsafe_path(self):
        profile = self.profile()
        profile["web_root"] = "/var/www; include /tmp/evil"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsafe characters"):
                render(profile_path, root / "output")

    def test_rejects_non_local_callback(self):
        profile = self.profile()
        profile["auth_callback"] = "https://example.test/auth"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "local"):
                render(profile_path, root / "output")


if __name__ == "__main__":
    unittest.main()
