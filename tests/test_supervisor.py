import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.restream_supervisor import RestreamSupervisor
from app.supervisor_client import SupervisorClient, SupervisorUnavailable


class _FakeSocket:
    def __init__(self):
        self.sent = b""
        self.response = b'{"success":true,"message":"ok"}\n'

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, path):
        self.path = path

    def sendall(self, value):
        self.sent += value

    def recv(self, size):
        response, self.response = self.response, b""
        return response


class SupervisorClientTests(unittest.TestCase):
    def test_client_sends_only_identifiers(self):
        fake_socket = _FakeSocket()
        with mock.patch("app.supervisor_client.socket.socket", return_value=fake_socket):
            result = SupervisorClient("/run/supervisor.sock").request("start", 3, 1)

        self.assertTrue(result["success"])
        self.assertEqual(json.loads(fake_socket.sent), {
            "action": "start",
            "field_id": 3,
            "url_index": 1,
        })

    def test_unavailable_socket_has_safe_error(self):
        with tempfile.TemporaryDirectory() as directory:
            client = SupervisorClient(Path(directory) / "missing.sock")
            with self.assertRaisesRegex(SupervisorUnavailable, "unavailable"):
                client.request("stop", 1)


class SupervisorValidationTests(unittest.TestCase):
    def setUp(self):
        self.supervisor = RestreamSupervisor()

    def test_source_uses_local_rtmp_without_hls_latency(self):
        settings = mock.Mock(local_rtmp_origin="rtmp://127.0.0.1")
        supervisor = RestreamSupervisor(settings)

        self.assertEqual(
            supervisor.source_url(6, "court-six"),
            "rtmp://127.0.0.1/place6/court-six",
        )

    def test_rejects_invalid_requests_before_dispatch(self):
        for request in (
            {"action": "shell", "field_id": 1},
            {"action": "start", "field_id": 0},
            {"action": "start", "field_id": True},
            {"action": "delete_destination", "field_id": 1},
        ):
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    self.supervisor.handle(request)

    def test_dispatches_delete_without_destination_data(self):
        with mock.patch.object(
            self.supervisor,
            "delete_destination",
            return_value={"success": True},
        ) as delete:
            result = self.supervisor.handle({
                "action": "delete_destination",
                "field_id": 4,
                "url_index": 2,
            })
        self.assertTrue(result["success"])
        delete.assert_called_once_with(4, 2)


if __name__ == "__main__":
    unittest.main()
