"""Public worker lifecycle and guarded launchd controller behavior."""

import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from local_service import control, launchd
from local_service.model import LABEL, PORT, RECORD_NAME, ROOT, STOP, TARGET, public_record, read_record


class LocalWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name) / "service"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "local_service", "serve", "--port", "0",
             "--state", str(self.state)], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.record = read_record(self.state)
            if self.record:
                return
            if self.process.poll() is not None:
                self.fail(self.process.stderr.read())
            time.sleep(0.02)
        self.fail("Worker did not publish ownership after binding")

    def tearDown(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.communicate(timeout=5)
        self.temporary.cleanup()

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.record["port"], timeout=2)
        try:
            connection.request(method, path, headers=headers or {}, body=body)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_preview_content_health_and_authenticated_exit(self):
        self.assertEqual((self.state / RECORD_NAME).stat().st_mode & 0o777, 0o600)
        status, raw = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"ok": True, **public_record(self.record)})
        self.assertNotIn(self.record["token"].encode(), raw)
        self.assertIn(b"mwan3-adaptive", self.request("GET", "/")[1])
        self.assertFalse(json.loads(self.request("GET", "/api/ac/status")[1])["hardware_enabled"])
        status, raw = self.request("POST", "/api/ac/commands/temp_up",
                                   {"Content-Type": "application/json"}, b'{"preview":true}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["delivery"], "dry_run")
        status, _ = self.request("POST", "/api/ac/commands/temp_up",
                                 {"Content-Type": "application/json"}, b'{"preview":false}')
        self.assertEqual(status, 409)
        self.assertEqual(self.request("POST", STOP)[0], 403)
        headers = {"Authorization": f"Bearer {self.record['token']}",
                   "Origin": "https://example.com"}
        self.assertEqual(self.request("POST", STOP, headers)[0], 403)
        del headers["Origin"]
        self.assertEqual(self.request("POST", STOP, headers)[0], 202)
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertFalse((self.state / RECORD_NAME).exists())

    def test_foreign_host_rejected_and_failed_bind_preserves_record(self):
        self.assertEqual(self.request("GET", "/", {"Host": "example.com"})[0], 403)
        before = (self.state / RECORD_NAME).read_bytes()
        result = subprocess.run(
            [sys.executable, "-m", "local_service", "serve", "--port", str(self.record["port"]),
             "--state", str(self.state)], cwd=ROOT, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.state / RECORD_NAME).read_bytes(), before)
        self.assertEqual(self.request("GET", "/api/health")[0], 200)


class ControllerTests(unittest.TestCase):
    def record(self):
        return {"service": LABEL, "root": str(ROOT), "uid": os.getuid(),
                "pid": 1234, "port": PORT, "instance": "a" * 64, "token": "b" * 64}

    def test_plist_uses_owned_foreground_interpreter_and_boot_supervision(self):
        config = launchd.configuration()
        self.assertEqual(config["ProgramArguments"][:4],
                         [str(ROOT / ".venv/bin/python"), "-m", "local_service", "serve"])
        self.assertEqual(config["WorkingDirectory"], str(ROOT))
        self.assertTrue(config["KeepAlive"])
        self.assertTrue(config["RunAtLoad"])
        self.assertEqual(config["Umask"], 0o077)
        self.assertEqual(config["ThrottleInterval"], 10)
        self.assertEqual(config["ExitTimeOut"], 20)
        for key in ("WorkingDirectory", "UserName", "ProgramArguments"):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "another workspace"):
                launchd.assert_owned_configuration({**config, key: "foreign"})

    def test_status_requires_enabled_job_with_matching_http_pid(self):
        record = self.record()
        with mock.patch.object(launchd, "installed_configuration", return_value=launchd.configuration()), \
                mock.patch.object(launchd, "job", return_value={"loaded": True, "pid": record["pid"]}), \
                mock.patch.object(launchd, "enabled", return_value=True) as enabled, \
                mock.patch.object(control, "owned_runtime", return_value=record), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(control.status())
            self.assertNotIn(record["token"], output.getvalue())
            enabled.return_value = False
            self.assertFalse(control.status())

    def test_foreign_listener_is_never_stopped(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with mock.patch.object(control, "PORT", listener.getsockname()[1]), \
                    mock.patch.object(control, "health", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "unrecognized listener"):
                    control.assert_port_available_or_owned()

    def test_mismatched_runtime_identity_fails_closed(self):
        record = self.record()
        live = {"ok": True, **public_record(record), "pid": record["pid"] + 1}
        with mock.patch.object(control, "health", return_value=live), \
                mock.patch.object(control, "read_record", return_value=record):
            with self.assertRaisesRegex(RuntimeError, "do not match"):
                control.owned_runtime()

    def test_stop_disables_before_unloading_and_keeps_project(self):
        with mock.patch.object(launchd, "preflight"), \
                mock.patch.object(launchd, "installed_configuration", return_value=launchd.configuration()), \
                mock.patch.object(launchd, "job", return_value={"loaded": True}), \
                mock.patch.object(control, "assert_port_available_or_owned", return_value=None), \
                mock.patch.object(launchd, "command") as command:
            control.stop()
        self.assertEqual(command.call_args_list, [
            mock.call("/bin/launchctl", "disable", TARGET, privileged=True),
            mock.call("/bin/launchctl", "bootout", TARGET, privileged=True)])

    def test_privilege_failure_precedes_any_runtime_interruption(self):
        with mock.patch.object(launchd, "prepare"), \
                mock.patch.object(control, "assert_port_available_or_owned", return_value=self.record()), \
                mock.patch.object(launchd, "command", side_effect=RuntimeError("permission denied")), \
                mock.patch.object(control, "stop_owned") as stop:
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                control.install()
            stop.assert_not_called()

    def test_restart_uses_graceful_stop_and_leaves_keepalive_enabled(self):
        before = self.record()
        after = {**before, "pid": 5678, "instance": "c" * 64}
        with mock.patch.object(launchd, "preflight"), \
                mock.patch.object(control, "status", return_value=True), \
                mock.patch.object(control, "stop_owned", return_value=before), \
                mock.patch.object(control, "wait_ready", return_value=after) as wait, \
                mock.patch.object(launchd, "command") as command:
            control.restart()
            wait.assert_called_once_with(previous=before)
            command.assert_not_called()
