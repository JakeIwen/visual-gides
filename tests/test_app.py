"""Behavior tests for the visual-guides A/C HTTP API."""

from __future__ import annotations

import http.client
import io
import json
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
from dataclasses import replace


from ac_ir import COMMANDS, Command, format_mode2
from app import AcIrServer, AppConfig, main, read_token


TEST_COMMAND = Command(
    name="power_toggle",
    label="Power",
    encoding="test fixture",
    carrier_hz=38_000,
    durations_us=(9000, 4500, 560),
    source="test fixture only",
)


class FakeTransport:
    def __init__(
        self, error: Exception | None = None, delay_seconds: float = 0
    ) -> None:
        self.commands: list[Command] = []
        self.error = error
        self.delay_seconds = delay_seconds

    def send(self, command: Command) -> None:
        self.commands.append(command)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.error:
            raise self.error


class RunningServer:
    def __init__(self, config: AppConfig) -> None:
        self.server = AcIrServer(("127.0.0.1", 0), config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "RunningServer":
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    @property
    def port(self) -> int:
        return self.server.server_port

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object] | bytes, dict[str, str]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        if response_headers.get("Content-Type", "").startswith("application/json"):
            return response.status, json.loads(data), response_headers
        return response.status, data, response_headers


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.static_root = Path(self.temp_dir.name)
        (self.static_root / "guides").mkdir()
        (self.static_root / "index.html").write_text("index", encoding="utf-8")
        (self.static_root / "style.css").write_text("body {}", encoding="utf-8")
        (self.static_root / "app.js").write_text("void 0;", encoding="utf-8")
        (self.static_root / "icon.svg").write_text("<svg/>", encoding="utf-8")
        (self.static_root / "steps.txt").write_text("steps", encoding="utf-8")
        (self.static_root / "guides" / "air-conditioner.html").write_text(
            "air conditioner", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def config(self, **overrides: object) -> AppConfig:
        config = AppConfig(
            static_root=self.static_root,
            commands={TEST_COMMAND.name: TEST_COMMAND},
            log_requests=False,
        )
        return replace(config, **overrides)

    def test_get_endpoints_are_read_only_and_do_not_transmit(self) -> None:
        transport = FakeTransport()
        config = self.config(
            hardware_enabled=True,
            token="a" * 24,
            transport=transport,
        )
        with RunningServer(config) as server:
            health_status, health, _ = server.request("GET", "/api/health")
            status_code, status, _ = server.request("GET", "/api/ac/status")
            commands_code, commands, _ = server.request("GET", "/api/ac/commands")

        self.assertEqual(health_status, 200)
        self.assertEqual(health, {"ok": True, "service": "visual-guides"})
        self.assertEqual(status_code, 200)
        self.assertEqual(status["ac_state"], "unknown")
        self.assertNotIn("token", status)
        self.assertEqual(commands_code, 200)
        self.assertEqual(commands["commands"][0]["name"], "power_toggle")
        self.assertEqual(transport.commands, [])

    def test_preview_is_default_safe_behavior_and_needs_no_token(self) -> None:
        transport = FakeTransport()
        with RunningServer(self.config(transport=transport)) as server:
            status, payload, _ = server.request(
                "POST",
                "/api/ac/commands/power_toggle",
                body=b'{"preview":true}',
                headers={"Content-Type": "application/json"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["delivery"], "dry_run")
        self.assertFalse(payload["confirmed"])
        self.assertEqual(payload["waveform"]["durations_us"], [9000, 4500, 560])
        self.assertEqual(transport.commands, [])

    def test_real_send_requires_hardware_auth_and_same_origin(self) -> None:
        transport = FakeTransport()
        config = self.config(
            hardware_enabled=True,
            token="valid-token-that-is-long-enough",
            transport=transport,
            min_transmit_interval=0,
        )
        body = b'{"preview":false}'
        with RunningServer(config) as server:
            base_headers = {"Content-Type": "application/json"}
            unauthorized, _, _ = server.request(
                "POST", "/api/ac/commands/power_toggle", body, base_headers
            )
            cross_site, _, _ = server.request(
                "POST",
                "/api/ac/commands/power_toggle",
                body,
                {
                    **base_headers,
                    "Authorization": "Bearer valid-token-that-is-long-enough",
                    "Origin": "https://attacker.example",
                },
            )
            sent, payload, _ = server.request(
                "POST",
                "/api/ac/commands/power_toggle",
                body,
                {
                    **base_headers,
                    "Authorization": "Bearer valid-token-that-is-long-enough",
                    "Origin": f"http://127.0.0.1:{server.port}",
                },
            )

        self.assertEqual(unauthorized, 401)
        self.assertEqual(cross_site, 403)
        self.assertEqual(sent, 202)
        self.assertEqual(payload["delivery"], "transmitted")
        self.assertFalse(payload["confirmed"])
        self.assertEqual(transport.commands, [TEST_COMMAND])

    def test_disabled_hardware_and_bad_bodies_never_transmit(self) -> None:
        transport = FakeTransport()
        with RunningServer(self.config(transport=transport)) as server:
            disabled, _, _ = server.request(
                "POST",
                "/api/ac/commands/power_toggle",
                b'{"preview":false}',
                {"Content-Type": "application/json"},
            )
            wrong_type, _, _ = server.request(
                "POST",
                "/api/ac/commands/power_toggle",
                b'{"preview":"false"}',
                {"Content-Type": "application/json"},
            )
            extra_field, _, _ = server.request(
                "POST",
                "/api/ac/commands/power_toggle",
                b'{"preview":true,"repeat":99}',
                {"Content-Type": "application/json"},
            )

        self.assertEqual(disabled, 409)
        self.assertEqual(wrong_type, 400)
        self.assertEqual(extra_field, 400)
        self.assertEqual(transport.commands, [])

    def test_ambiguous_transport_failure_still_starts_rate_limit(self) -> None:
        transport = FakeTransport(
            RuntimeError("uncertain send"), delay_seconds=0.03
        )
        config = self.config(
            hardware_enabled=True,
            token="valid-token-that-is-long-enough",
            transport=transport,
            min_transmit_interval=0.02,
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer valid-token-that-is-long-enough",
        }
        with RunningServer(config) as server:
            first, first_body, _ = server.request(
                "POST", "/api/ac/commands/power_toggle", b'{"preview":false}', headers
            )
            second, _, _ = server.request(
                "POST", "/api/ac/commands/power_toggle", b'{"preview":false}', headers
            )

        self.assertEqual(first, 502)
        self.assertEqual(first_body["error"], "IR transmission failed")
        self.assertEqual(second, 429)
        self.assertEqual(len(transport.commands), 1)

    def test_static_paths_are_bounded_to_static_root(self) -> None:
        outside = self.static_root.parent / "secret-test-file"
        outside.write_text("secret", encoding="utf-8")
        self.addCleanup(outside.unlink)
        with RunningServer(self.config()) as server:
            index_status, index, _ = server.request("GET", "/")
            guide_status, guide, _ = server.request("GET", "/guides/air-conditioner.html")
            traversal_status, _, _ = server.request("GET", "/%2e%2e/secret-test-file")

        self.assertEqual((index_status, index), (200, b"index"))
        self.assertEqual((guide_status, guide), (200, b"air conditioner"))
        self.assertEqual(traversal_status, 404)

    def test_static_assets_have_browser_usable_content_types(self) -> None:
        with RunningServer(self.config()) as server:
            css_status, _, css_headers = server.request("GET", "/style.css")
            js_status, _, js_headers = server.request("GET", "/app.js")
            svg_status, _, svg_headers = server.request("GET", "/icon.svg")
            txt_status, _, txt_headers = server.request("GET", "/steps.txt")

        self.assertEqual(css_status, 200)
        self.assertEqual(css_headers["Content-Type"], "text/css; charset=utf-8")
        self.assertEqual(js_status, 200)
        self.assertEqual(js_headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(svg_status, 200)
        self.assertEqual(svg_headers["Content-Type"], "image/svg+xml")
        self.assertEqual(txt_status, 200)
        self.assertEqual(txt_headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(css_headers["X-Content-Type-Options"], "nosniff")

    def test_rejected_request_closes_connection(self) -> None:
        with RunningServer(self.config()) as server:
            status, _, headers = server.request(
                "POST",
                "/api/ac/commands/not-a-command",
                b'{"preview":true}',
                {"Content-Type": "application/json"},
            )

        self.assertEqual(status, 404)
        self.assertEqual(headers["Connection"], "close")


class WaveformTests(unittest.TestCase):
    def test_mode2_alternates_pulses_and_spaces(self) -> None:
        self.assertEqual(
            format_mode2((9000, 4500, 560)),
            "pulse 9000\nspace 4500\npulse 560\n",
        )

    def test_mode2_rejects_invalid_or_even_length_data(self) -> None:
        with self.assertRaises(ValueError):
            format_mode2((9000, 4500))
        with self.assertRaises(TypeError):
            format_mode2((9000, 4500, True))

    def test_candidate_nec_waveform_uses_converted_lsb_first_bytes(self) -> None:
        power = COMMANDS["power_toggle"]
        self.assertIn("physical validation pending", power.source)
        self.assertEqual(power.linux_scancode, "necx:0x08f511")
        self.assertEqual(len(power.source_urls), 2)
        self.assertEqual(len(power.durations_us), 67)
        # Logical 0x08 begins on the wire with 0, 0, 0, 1 (LSB first).
        first_byte_spaces = power.durations_us[3:18:2]
        self.assertEqual(first_byte_spaces[:4], (486, 486, 486, 1615))

    def test_catalog_excludes_unproven_absolute_state_commands(self) -> None:
        self.assertEqual(
            set(COMMANDS),
            {
                "power_toggle",
                "temp_up",
                "temp_down",
                "mode_cool",
                "energy_saver",
                "mode_fan",
                "fan_auto",
                "fan_down",
                "fan_up",
                "sleep",
                "timer",
            },
        )


class TokenTests(unittest.TestCase):
    def test_token_file_must_be_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("valid-token-that-is-long-enough\n", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "group or other"):
                read_token(path)
            path.chmod(0o600)
            self.assertEqual(read_token(path), "valid-token-that-is-long-enough")

    def test_token_contents_are_bounded_and_whitespace_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("two tokens separated by spaces", encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "one 24-512 character token"):
                read_token(path)


class CliTests(unittest.TestCase):
    def private_token_file(self, directory: str) -> Path:
        path = Path(directory) / "token"
        path.write_text("valid-token-that-is-long-enough\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_send_uses_running_service_transport_once(self) -> None:
        transport = FakeTransport()
        with tempfile.TemporaryDirectory() as directory:
            token_file = self.private_token_file(directory)
            config = AppConfig(
                static_root=Path(directory),
                commands=COMMANDS,
                hardware_enabled=True,
                token="valid-token-that-is-long-enough",
                transport=transport,
                min_transmit_interval=0,
                log_requests=False,
            )
            with RunningServer(config) as server:
                with mock.patch("sys.stdout", new=io.StringIO()) as output:
                    result = main(
                        [
                            "--port",
                            str(server.port),
                            "--enable-hardware",
                            "--token-file",
                            str(token_file),
                            "--send",
                            "temp_up",
                        ]
                    )

        self.assertEqual(result, 0)
        self.assertEqual(transport.commands, [COMMANDS["temp_up"]])
        self.assertEqual(json.loads(output.getvalue())["delivery"], "transmitted")

    def test_send_to_disabled_service_never_transmits_or_retries(self) -> None:
        transport = FakeTransport()
        with tempfile.TemporaryDirectory() as directory:
            token_file = self.private_token_file(directory)
            config = AppConfig(
                static_root=Path(directory),
                commands=COMMANDS,
                transport=transport,
                log_requests=False,
            )
            with RunningServer(config) as server:
                with self.assertRaisesRegex(SystemExit, "hardware transmission is disabled"):
                    main(
                        [
                            "--port",
                            str(server.port),
                            "--enable-hardware",
                            "--token-file",
                            str(token_file),
                            "--send",
                            "temp_up",
                        ]
                    )

        self.assertEqual(transport.commands, [])

    def test_unknown_send_fails_before_token_read_or_network(self) -> None:
        with mock.patch("app.read_token") as read_token_mock:
            with mock.patch("app.http.client.HTTPConnection") as connection_mock:
                with self.assertRaisesRegex(SystemExit, "unknown command"):
                    main(["--send", "unsupported_command"])

        read_token_mock.assert_not_called()
        connection_mock.assert_not_called()

    def test_send_transport_error_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_file = self.private_token_file(directory)
            connection = mock.Mock()
            connection.request.side_effect = OSError("connection failed")
            with mock.patch(
                "app.http.client.HTTPConnection", return_value=connection
            ) as connection_class:
                with self.assertRaisesRegex(SystemExit, "connection failed"):
                    main(
                        [
                            "--token-file",
                            str(token_file),
                            "--enable-hardware",
                            "--send",
                            "temp_up",
                        ]
                    )

        connection_class.assert_called_once()
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    def test_send_requires_explicit_hardware_acknowledgement_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_file = self.private_token_file(directory)
            with mock.patch("app.http.client.HTTPConnection") as connection_mock:
                with self.assertRaisesRegex(SystemExit, "requires explicit"):
                    main(
                        [
                            "--token-file",
                            str(token_file),
                            "--send",
                            "temp_up",
                        ]
                    )

        connection_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
