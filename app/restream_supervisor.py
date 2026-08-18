"""Dedicated owner of outgoing FFmpeg restream processes."""

import json
import os
import signal
import socketserver
import subprocess
import threading
from datetime import datetime, timezone

import psutil

from .config_store import load_and_validate_config
from .settings import SETTINGS


MAX_REQUEST_SIZE = 4096


class RestreamSupervisor:
    def __init__(self, settings=SETTINGS):
        self.settings = settings
        self.lock = threading.RLock()

    def fields(self):
        return load_and_validate_config(self.settings.config_file).get("fields", {})

    def process(self, field_id, url_index):
        pid_file = self.settings.pid_file(field_id, url_index)
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            process = psutil.Process(pid)
            if process.is_running() and "ffmpeg" in process.name().lower():
                return process
        except (
            FileNotFoundError,
            ValueError,
            psutil.NoSuchProcess,
            psutil.AccessDenied,
        ):
            pass
        if pid_file.exists():
            pid_file.unlink()
        return None

    def selected_indices(self, field_id, url_index):
        field = self.fields().get(str(field_id))
        if not isinstance(field, dict):
            raise ValueError("field not found")
        destinations = field.get("restream_urls", [])
        if url_index is None:
            return field, list(range(len(destinations)))
        if type(url_index) is not int or not 0 <= url_index < len(destinations):
            raise ValueError("invalid destination index")
        return field, [url_index]

    def source_url(self, field_id, stream_key):
        """Return the local RTMP source without adding HLS latency."""
        return (
            f"{self.settings.local_rtmp_origin}/"
            f"place{field_id}/{stream_key}"
        )

    def start(self, field_id, url_index=None):
        with self.lock:
            self.settings.ensure_runtime_directories()
            field, indices = self.selected_indices(field_id, url_index)
            destinations = field.get("restream_urls", [])
            stream_key = field.get("stream_key") or f"stream{field_id}"
            source = self.source_url(field_id, stream_key)
            started = []
            already_running = []

            for index in indices:
                if self.process(field_id, index) is not None:
                    already_running.append(index)
                    continue

                log_file = self.settings.log_file(field_id, index)
                log_fd = os.open(
                    log_file,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                    0o600,
                )
                os.chmod(log_file, 0o600)
                log_handle = os.fdopen(
                    log_fd,
                    "a",
                    encoding="utf-8",
                    buffering=1,
                )
                try:
                    log_handle.write(
                        f"\n=== Restream started at "
                        f"{datetime.now(timezone.utc).isoformat()} ===\n"
                    )
                    log_handle.write(f"Source: {source}\n")
                    log_handle.write("Destination: [configured]\n")
                    log_handle.flush()
                    process = subprocess.Popen(
                        [
                            str(self.settings.ffmpeg_bin),
                            "-hide_banner",
                            "-loglevel", "warning",
                            "-nostats",
                            "-i", source,
                            "-c", "copy",
                            "-f", "flv",
                            "-flvflags", "no_duration_filesize",
                            destinations[index],
                        ],
                        stdout=log_handle,
                        stderr=log_handle,
                        stdin=subprocess.DEVNULL,
                        close_fds=True,
                    )
                finally:
                    log_handle.close()

                pid_file = self.settings.pid_file(field_id, index)
                pid_file.write_text(str(process.pid), encoding="utf-8")
                pid_file.chmod(0o600)
                started.append(index)

            return {
                "success": bool(started) or bool(already_running),
                "message": "restream start processed",
                "started": started,
                "already_running": already_running,
            }

    def stop(self, field_id, url_index=None):
        with self.lock:
            _, indices = self.selected_indices(field_id, url_index)
            stopped = []
            not_running = []
            for index in indices:
                process = self.process(field_id, index)
                if process is None:
                    not_running.append(index)
                    continue
                process.terminate()
                try:
                    process.wait(timeout=10)
                except psutil.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                pid_file = self.settings.pid_file(field_id, index)
                if pid_file.exists():
                    pid_file.unlink()
                stopped.append(index)
            return {
                "success": True,
                "message": "restream stop processed",
                "stopped": stopped,
                "not_running": not_running,
            }

    def restart(self, field_id, url_index=None):
        self.stop(field_id, url_index)
        return self.start(field_id, url_index)

    def delete_destination(self, field_id, url_index):
        """Stop a field and compact supervisor-owned artifacts before deletion."""
        with self.lock:
            field, _ = self.selected_indices(field_id, url_index)
            destination_count = len(field.get("restream_urls", []))
            self.stop(field_id)

            deleted_log = self.settings.log_file(field_id, url_index)
            if deleted_log.exists():
                deleted_log.unlink()
            for index in range(url_index + 1, destination_count):
                old_log = self.settings.log_file(field_id, index)
                new_log = self.settings.log_file(field_id, index - 1)
                if old_log.exists():
                    os.replace(old_log, new_log)

            return {
                "success": True,
                "message": "destination runtime state removed",
            }

    def handle(self, request):
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        action = request.get("action")
        field_id = request.get("field_id")
        url_index = request.get("url_index")
        if action not in {"start", "stop", "restart", "delete_destination"}:
            raise ValueError("unsupported action")
        if type(field_id) is not int or not 1 <= field_id <= 16:
            raise ValueError("invalid field ID")
        if url_index is not None and (type(url_index) is not int or url_index < 0):
            raise ValueError("invalid destination index")
        if action == "delete_destination" and url_index is None:
            raise ValueError("destination index is required")
        return getattr(self, action)(field_id, url_index)


SUPERVISOR = RestreamSupervisor()


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        raw = self.rfile.readline(MAX_REQUEST_SIZE + 1)
        if len(raw) > MAX_REQUEST_SIZE or not raw.endswith(b"\n"):
            response = {"success": False, "message": "invalid request"}
        else:
            try:
                request = json.loads(raw.decode("utf-8"))
                response = SUPERVISOR.handle(request)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                response = {"success": False, "message": str(error)}
            except Exception:
                response = {"success": False, "message": "supervisor error"}
        self.wfile.write(
            (json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8")
        )


class UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    SETTINGS.ensure_runtime_directories()
    socket_path = SETTINGS.supervisor_socket
    if socket_path.exists():
        socket_path.unlink()

    server = UnixServer(str(socket_path), RequestHandler)
    socket_path.chmod(0o600)

    def stop_server(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        if socket_path.exists():
            socket_path.unlink()


if __name__ == "__main__":
    main()
