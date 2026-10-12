import importlib.util
from contextlib import contextmanager
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock


PATH = Path(__file__).with_name("deploy.py")
SPEC = importlib.util.spec_from_file_location("visual_guides_deploy", PATH)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class DeployTests(unittest.TestCase):
    @contextmanager
    def installed_project(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "source"
            source.mkdir()
            (source / "app.py").write_text("pass\n")
            (source / "visual-guides.service").write_text(deploy.UNIT_TEMPLATE)
            manifest = deploy.build_manifest(source)
            root = workspace / "live"
            releases = root / "releases"
            releases.mkdir(parents=True)
            deploy.extract_release(io.BytesIO(deploy.make_archive(source, manifest)),
                                   manifest, releases / manifest["release"])
            (releases / ("b" * 24)).mkdir()
            (root / "current").symlink_to(f"releases/{manifest['release']}")
            (root / "previous").symlink_to("releases/" + "b" * 24)
            marker = root / ".managed-by-visual-guides-deploy"
            marker.write_text("schema=1\n")
            unit = workspace / "visual-guides.service"
            unit.write_text(deploy.UNIT_TEMPLATE)

            def systemctl(*args, **kwargs):
                return mock.Mock(stdout={"is-active": "active", "is-enabled": "enabled"}
                                 .get(args[0], ""), returncode=0)

            with mock.patch.multiple(deploy, ROOT=root, RELEASES=releases,
                                     CURRENT=root / "current", PREVIOUS=root / "previous",
                                     UNIT_PATH=unit, OWNER_MARKER=marker), \
                    mock.patch.object(deploy, "systemctl", side_effect=systemctl) as system, \
                    mock.patch.object(deploy, "listener_owner", return_value="owned listener"):
                yield manifest, root, system

    def test_unchanged_healthy_deployment_preserves_rollback_without_restart(self):
        with self.installed_project() as (manifest, root, system), \
                mock.patch.object(deploy, "health_ok", return_value=True), \
                mock.patch.object(deploy, "ensure_private_token") as token:
            previous = (root / "previous").readlink()
            result = deploy.remote_apply(manifest)
            self.assertEqual(result["current"], f"releases/{manifest['release']}")
            self.assertEqual((root / "previous").readlink(), previous)
            self.assertEqual([call.args[0] for call in system.call_args_list],
                             ["is-active", "is-enabled"])
            token.assert_not_called()

    def test_unchanged_deployment_still_rejects_tampered_installed_content(self):
        with self.installed_project() as (manifest, root, system), \
                mock.patch.object(deploy, "health_ok", return_value=True) as health:
            (root / "current" / "app.py").write_text("unexpected code\n")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                deploy.remote_apply(manifest)
            health.assert_not_called()
            self.assertEqual([call.args[0] for call in system.call_args_list],
                             ["is-active", "is-enabled"])

    def test_repairing_same_release_keeps_prior_rollback(self):
        with self.installed_project() as (manifest, root, system), \
                mock.patch.object(deploy, "health_ok", side_effect=[False, True]) as health, \
                mock.patch.object(deploy, "ensure_private_token"), \
                mock.patch.object(deploy.pwd, "getpwnam"), \
                mock.patch.object(deploy.os, "chown"):
            previous = (root / "previous").readlink()
            deploy.remote_apply(manifest)
            self.assertEqual((root / "previous").readlink(), previous)
            self.assertEqual(sum(call.args[0] == "restart" for call in system.call_args_list), 1)
            self.assertEqual(health.call_args_list, [mock.call(attempts=1), mock.call()])

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
