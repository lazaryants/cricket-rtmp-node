import json
import os
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from app.monitoring import health_snapshot, metrics_snapshot, parse_rtmp_stat
from app.settings import Settings


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.hls_root = self.root / "hls"
        self.hls_root.mkdir()
        self.ffmpeg = self.root / "ffmpeg"
        self.ffmpeg.write_text("#!/bin/sh\n", encoding="utf-8")
        self.ffmpeg.chmod(0o755)

        environment = {
            "CRICKET_RTMP_ROOT": str(self.root),
            "CRICKET_RTMP_HLS_ROOT": str(self.hls_root),
            "CRICKET_RTMP_FFMPEG": str(self.ffmpeg),
            "CRICKET_RTMP_STAT_URL": "http://127.0.0.1:8090/stat",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.settings = Settings()

        self.settings.config_file.parent.mkdir(parents=True)
        self.settings.config_file.write_text(
            json.dumps({
                "schema_version": 1,
                "fields": {
                    "1": {
                        "enabled": True,
                        "stream_key": "stream1",
                        "key": "publish-secret",
                        "publish_auth_enabled": True,
                        "restream_urls": [
                            "rtmp://destination.example/live/private-token",
                        ],
                    },
                },
            }),
            encoding="utf-8",
        )

        place1 = self.hls_root / "place1"
        place1.mkdir()
        segment = place1 / "stream1-1.ts"
        segment.write_bytes(b"segment")
        os.utime(segment, (time.time(), time.time()))

    def tearDown(self):
        self.temporary_directory.cleanup()

    @patch("app.monitoring.rtmp_snapshot")
    def test_metrics_are_safe_and_report_active_hls(self, mocked_rtmp):
        mocked_rtmp.return_value = {
            "reachable": True,
            "active_streams": 1,
            "clients": 1,
            "applications": {"place1": {"streams": 1, "clients": 1}},
        }
        metrics = metrics_snapshot(self.settings)
        serialized = json.dumps(metrics)

        self.assertEqual(metrics["hls"]["places"]["1"]["state"], "active")
        self.assertEqual(metrics["config"]["schema_version"], 1)
        self.assertEqual(metrics["config"]["publish_auth_enabled_places"], 1)
        self.assertNotIn("publish-secret", serialized)
        self.assertNotIn("private-token", serialized)
        self.assertNotIn("restream_urls", serialized)

    @patch("app.monitoring.rtmp_snapshot")
    def test_health_is_ok_when_dependencies_are_ready(self, mocked_rtmp):
        mocked_rtmp.return_value = {"reachable": True}
        health = health_snapshot(self.settings)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["checks"]["config"]["schema_version"], 1)
        self.assertTrue(all(check["ok"] for check in health["checks"].values()))

    def test_rtmp_metrics_are_server_side_and_do_not_expose_identity(self):
        root = ET.fromstring("""
            <rtmp>
              <server>
                <application>
                  <name>place16</name>
                  <live>
                    <stream>
                      <name>private-stream-key</name>
                      <time>9574060</time>
                      <bw_in>1567672</bw_in>
                      <bytes_in>1235480564</bytes_in>
                      <bw_audio>192712</bw_audio>
                      <bw_video>1374952</bw_video>
                      <client>
                        <address>203.0.113.10</address>
                        <publishing/>
                        <dropped>3</dropped>
                      </client>
                      <client>
                        <address>127.0.0.1</address>
                        <dropped>99</dropped>
                      </client>
                      <nclients>2</nclients>
                      <meta>
                        <video>
                          <width>1920</width>
                          <height>1080</height>
                          <frame_rate>30</frame_rate>
                          <codec>H264</codec>
                          <profile>High</profile>
                          <level>4.0</level>
                        </video>
                        <audio>
                          <codec>AAC</codec>
                          <profile>LC</profile>
                          <channels>2</channels>
                          <sample_rate>44100</sample_rate>
                        </audio>
                      </meta>
                    </stream>
                  </live>
                </application>
              </server>
            </rtmp>
        """)

        snapshot = parse_rtmp_stat(root)
        stream = snapshot["applications"]["place16"]["stream_metrics"][0]
        serialized = json.dumps(snapshot)

        self.assertEqual(snapshot["active_streams"], 1)
        self.assertEqual(snapshot["clients"], 2)
        self.assertEqual(stream["uptime_seconds"], 9574.1)
        self.assertEqual(stream["input_bitrate_bps"], 1567672)
        self.assertEqual(stream["video_bitrate_bps"], 1374952)
        self.assertEqual(stream["audio_bitrate_bps"], 192712)
        self.assertEqual(stream["publishers"], 1)
        self.assertEqual(stream["players"], 1)
        self.assertEqual(stream["publisher_dropped"], 3)
        self.assertEqual(stream["video"]["resolution"], "1920x1080")
        self.assertEqual(stream["video"]["source_fps"], 30.0)
        self.assertEqual(stream["audio"]["sample_rate_hz"], 44100)
        self.assertNotIn("private-stream-key", serialized)
        self.assertNotIn("203.0.113.10", serialized)

    def test_rtmp_metrics_tolerate_missing_metadata(self):
        root = ET.fromstring("""
            <rtmp>
              <server>
                <application>
                  <name>place2</name>
                  <live>
                    <stream>
                      <nclients>0</nclients>
                    </stream>
                  </live>
                </application>
              </server>
            </rtmp>
        """)

        stream = parse_rtmp_stat(root)[
            "applications"
        ]["place2"]["stream_metrics"][0]

        self.assertIsNone(stream["uptime_seconds"])
        self.assertIsNone(stream["input_bitrate_bps"])
        self.assertIsNone(stream["video"]["resolution"])
        self.assertIsNone(stream["audio"]["codec"])
        self.assertEqual(stream["publisher_dropped"], 0)


if __name__ == "__main__":
    unittest.main()
