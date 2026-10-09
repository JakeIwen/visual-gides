import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


PATH = Path(__file__).with_name("deploy.py")
SPEC = importlib.util.spec_from_file_location("visual_guides_deploy", PATH)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class DeployTests(unittest.TestCase):
    def test_artifact_allowlist_excludes_tests_docs_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("pass\n")
            (root / "worker.py").write_text("pass\n")
            (root / "test_app.py").write_text("secret\n")
            (root / "README.md").write_text("docs\n")
            (root / "api-token").write_text("secret\n")
            (root / "static").mkdir()
            (root / "static/index.html").write_text("ok\n")
            (root / "static/instructions.txt").write_text("ok\n")
            (root / "static/cache.tmp").write_text("no\n")
            self.assertEqual(
                deploy.artifact_paths(root),
                ["app.py", "static/index.html", "static/instructions.txt", "worker.py"],
            )

    def test_unit_has_fixed_safe_process_contract(self):
        unit = deploy.UNIT_TEMPLATE
        self.assertIn("User=pi", unit)
        self.assertIn("/usr/bin/python3 /home/pi/visual-guides/current/app.py --bind 0.0.0.0 --port 8791", unit)
        self.assertIn("--static-root /home/pi/visual-guides/current/static", unit)
        self.assertIn("--token-file /home/pi/.config/visual-guides/api-token", unit)
        self.assertNotIn("Environment=", unit)
        self.assertNotIn("gpio", unit.lower())

    def test_listener_discovery_fails_closed(self):
        result = mock.Mock(returncode=1, stderr="ss failed", stdout="")
        with mock.patch.object(deploy.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "listener discovery failed"):
                deploy.listener_owner()

    def test_reused_release_verifies_every_file_and_rejects_extras(self):
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory)
            app_data = b"pass\n"
            manifest = {
                "schema": 1,
                "files": {"app.py": deploy.sha256(app_data)},
                "unit_sha256": "0" * 64,
            }
            manifest["release"] = deploy.sha256(deploy.canonical(manifest))[:24]
            (release / "app.py").write_bytes(app_data)
            (release / "manifest.json").write_bytes(deploy.canonical(manifest) + b"\n")
            deploy.verify_release(release, manifest)
            (release / "app.py").write_bytes(b"tampered\n")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                deploy.verify_release(release, manifest)
            (release / "app.py").write_bytes(app_data)
            (release / "extra.py").write_text("unexpected\n")
            with self.assertRaisesRegex(ValueError, "file set"):
                deploy.verify_release(release, manifest)

    def test_restore_deployment_restores_links_unit_and_service_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "current"
            previous = root / "previous"
            unit = root / "visual-guides.service"
            current.symlink_to("releases/new")
            previous.symlink_to("releases/old")
            unit.write_text("new unit")
            before = {
                "current": "releases/old",
                "previous": "releases/older",
                "enabled": "enabled",
                "active": "active",
            }
            with mock.patch.multiple(
                deploy, CURRENT=current, PREVIOUS=previous, UNIT_PATH=unit
            ):
                with mock.patch.object(deploy, "systemctl") as systemctl:
                    deploy.restore_deployment(before, b"prior unit")

            self.assertEqual(current.readlink().as_posix(), "releases/old")
            self.assertEqual(previous.readlink().as_posix(), "releases/older")
            self.assertEqual(unit.read_bytes(), b"prior unit")
            self.assertEqual(
                [call.args for call in systemctl.call_args_list],
                [
                    ("daemon-reload",),
                    ("enable", deploy.UNIT_NAME),
                    ("restart", deploy.UNIT_NAME),
                ],
            )


if __name__ == "__main__":
    unittest.main()
