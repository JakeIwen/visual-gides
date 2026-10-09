#!/usr/bin/env python3
"""Deploy the independent visual-guides service to vanpi.

The default ``status`` and ``check`` operations are read-only. ``apply`` is the
only mutating operation. It installs an immutable release and a fixed systemd
unit, but never configures GPIO, edits boot configuration, or reboots the Pi.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import secrets
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import time


PROJECT = Path(__file__).resolve().parent if "__file__" in globals() else None
ROOT = Path("/home/pi/visual-guides")
RELEASES = ROOT / "releases"
CURRENT = ROOT / "current"
PREVIOUS = ROOT / "previous"
UNIT_PATH = Path("/etc/systemd/system/visual-guides.service")
TOKEN = Path("/home/pi/.config/visual-guides/api-token")
OWNER_MARKER = ROOT / ".managed-by-visual-guides-deploy"
UNIT_NAME = "visual-guides.service"
PORT = 8791
MAX_ARCHIVE = 32 * 1024 * 1024
STATIC_SUFFIXES = {".css", ".gif", ".html", ".ico", ".jpeg", ".jpg", ".js", ".json", ".png", ".svg", ".txt", ".webp"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def artifact_paths(project):
    """Return the explicit code/static allowlist, with no generated content."""
    paths = []
    for path in project.glob("*.py"):
        if path.name != "deploy.py" and not path.name.startswith("test_") and not path.name.endswith("_test.py"):
            paths.append(path)
    static = project / "static"
    if static.is_dir():
        for path in static.rglob("*"):
            if path.is_file() and path.suffix.lower() in STATIC_SUFFIXES:
                paths.append(path)
    relative = sorted(path.relative_to(project).as_posix() for path in paths)
    if "app.py" not in relative:
        raise ValueError("app.py is required")
    return relative


def build_manifest(project):
    files = {}
    for relative in artifact_paths(project):
        path = project / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"refusing non-regular artifact: {relative}")
        files[relative] = sha256(path.read_bytes())
    unit = (project / "visual-guides.service").read_bytes()
    manifest = {"schema": 1, "files": files, "unit_sha256": sha256(unit)}
    manifest["release"] = sha256(canonical(manifest))[:24]
    return manifest


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def make_archive(project, manifest):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for relative in manifest["files"]:
            info = archive.gettarinfo(str(project / relative), relative)
            info.mode = 0o644
            with (project / relative).open("rb") as stream:
                archive.addfile(info, stream)
        data = canonical(manifest) + b"\n"
        info = tarfile.TarInfo("manifest.json")
        info.mode, info.size = 0o644, len(data)
        archive.addfile(info, io.BytesIO(data))
    if output.tell() > MAX_ARCHIVE:
        raise ValueError("release archive exceeds 32 MiB")
    return output.getvalue()


def run_ssh(target, mode, *, stdin=b"", manifest=None):
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]+", target) or target.startswith("-"):
        raise ValueError("invalid SSH target")
    source = Path(__file__).read_text()
    command = f"sudo -n /usr/bin/python3 -c {shlex.quote(source)} --remote-{mode}"
    if manifest is not None:
        command += " --manifest " + shlex.quote(canonical(manifest).decode())
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=5", "--", target, command],
            input=stdin, capture_output=True, timeout=45 if mode != "apply" else 90,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"SSH {mode} failed: {error}") from error
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()[-1200:]
        raise RuntimeError(f"remote {mode} failed ({result.returncode}): {detail}")
    return json.loads(result.stdout or b"{}")


def safe_manifest(raw):
    manifest = json.loads(raw) if isinstance(raw, str) else raw
    if manifest.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{24}", manifest.get("release", "")):
        raise ValueError("invalid release manifest")
    unsigned = {key: value for key, value in manifest.items() if key != "release"}
    if sha256(canonical(unsigned))[:24] != manifest["release"]:
        raise ValueError("release digest mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or "app.py" not in files:
        raise ValueError("invalid file manifest")
    for name, digest in files.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("unsafe manifest entry")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.get("unit_sha256", "")):
        raise ValueError("invalid unit digest")
    return manifest


def link_release(path):
    if not path.is_symlink():
        if path.exists():
            raise ValueError(f"refusing unmanaged path: {path}")
        return None
    target = os.readlink(path)
    if not re.fullmatch(r"releases/[0-9a-f]{24}", target):
        raise ValueError(f"refusing foreign release link: {path}")
    release = ROOT / target
    if not release.is_dir():
        raise ValueError(f"release link target is missing: {path}")
    return target


def regular_sha(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode):
        raise ValueError(f"refusing non-regular file: {path}")
    return sha256(path.read_bytes())


def systemctl(*args, check=True):
    return subprocess.run(["/usr/bin/systemctl", *args], capture_output=True, text=True, check=check, timeout=20)


def listener_owner():
    result = subprocess.run(["/usr/bin/ss", "-H", "-ltnp", f"sport = :{PORT}"], capture_output=True, text=True, timeout=10)
    if result.returncode:
        detail = result.stderr.strip()[-500:]
        raise RuntimeError(f"listener discovery failed ({result.returncode}): {detail}")
    return result.stdout.strip()


def inspect_remote(manifest=None):
    if ROOT.exists():
        allowed = {"releases", "current", "previous", OWNER_MARKER.name}
        unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
        if unexpected or (any(ROOT.iterdir()) and not OWNER_MARKER.is_file()):
            raise ValueError("refusing pre-existing unmanaged deployment root")
    current = link_release(CURRENT)
    previous = link_release(PREVIOUS)
    unit_sha = regular_sha(UNIT_PATH)
    active = systemctl("is-active", UNIT_NAME, check=False).stdout.strip()
    enabled = systemctl("is-enabled", UNIT_NAME, check=False).stdout.strip()
    listener = listener_owner()
    if listener and active != "active":
        raise ValueError(f"port {PORT} has a foreign listener")
    installed = None
    if current and manifest:
        installed = json.loads((ROOT / current / "manifest.json").read_text())
        safe_manifest(installed)
        verify_release(ROOT / current, installed)
    if unit_sha is not None and manifest:
        managed_hashes = {manifest["unit_sha256"]}
        if installed:
            managed_hashes.add(installed["unit_sha256"])
        if unit_sha not in managed_hashes or (not current and unit_sha != manifest["unit_sha256"]):
            raise ValueError("existing service unit is not managed by this checkout")
    return {"current": current, "previous": previous, "unit_sha256": unit_sha, "active": active, "enabled": enabled, "port_listening": bool(listener)}


def extract_release(archive_stream, manifest, destination):
    destination.mkdir(mode=0o755)
    seen = set()
    with tarfile.open(fileobj=archive_stream, mode="r|gz") as archive:
        for member in archive:
            if not member.isfile() or member.name not in {*manifest["files"], "manifest.json"} or member.name in seen:
                raise ValueError("archive contains an unexpected member")
            data = archive.extractfile(member).read()
            expected = sha256(data)
            if member.name == "manifest.json":
                if safe_manifest(data.decode()) != manifest:
                    raise ValueError("archive manifest mismatch")
            elif expected != manifest["files"][member.name]:
                raise ValueError(f"artifact checksum mismatch: {member.name}")
            path = destination / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)
            seen.add(member.name)
    if seen != {*manifest["files"], "manifest.json"}:
        raise ValueError("archive is incomplete")


def verify_release(release, manifest):
    """Verify all content in an existing immutable release before reuse."""
    expected = {*manifest["files"], "manifest.json"}
    found = set()
    for path in release.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"reused release contains a symlink: {path}")
        if path.is_file():
            found.add(path.relative_to(release).as_posix())
    if found != expected:
        raise ValueError("reused release file set does not match its manifest")
    if regular_sha(release / "manifest.json") != sha256(canonical(manifest) + b"\n"):
        raise ValueError("release ID collision or unmanaged release")
    for relative, digest in manifest["files"].items():
        if regular_sha(release / relative) != digest:
            raise ValueError(f"reused release checksum mismatch: {relative}")


def replace_link(path, target):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.symlink_to(target)
    os.replace(temporary, path)


def ensure_private_token():
    account = pwd.getpwnam("pi")
    TOKEN.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if TOKEN.exists():
        if regular_sha(TOKEN) is None:
            raise ValueError("token is not a regular file")
        value = TOKEN.read_text().strip()
        try:
            value.encode("ascii")
        except UnicodeEncodeError as error:
            raise ValueError("existing API token is not ASCII") from error
        if not 24 <= len(value) <= 512 or any(character.isspace() for character in value):
            raise ValueError("existing API token has an invalid format")
    else:
        fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
    os.chown(TOKEN.parent, account.pw_uid, account.pw_gid)
    os.chown(TOKEN, account.pw_uid, account.pw_gid)
    TOKEN.parent.chmod(0o700)
    TOKEN.chmod(0o600)


def health_ok():
    import urllib.request
    for _ in range(20):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2) as response:
                body = json.loads(response.read())
                if response.status == 200 and body == {"ok": True, "service": "visual-guides"}:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    return False


def restore_deployment(before, prior_unit):
    """Best-effort restoration of every service state changed during cutover."""
    if before["current"]:
        replace_link(CURRENT, before["current"])
    else:
        CURRENT.unlink(missing_ok=True)
    if before["previous"]:
        replace_link(PREVIOUS, before["previous"])
    else:
        PREVIOUS.unlink(missing_ok=True)
    if prior_unit is None:
        UNIT_PATH.unlink(missing_ok=True)
    else:
        temporary = UNIT_PATH.with_name(f".{UNIT_PATH.name}.{os.getpid()}.rollback")
        temporary.write_bytes(prior_unit)
        temporary.chmod(0o644)
        os.replace(temporary, UNIT_PATH)
    systemctl("daemon-reload")
    if before["enabled"] == "enabled":
        systemctl("enable", UNIT_NAME)
    else:
        systemctl("disable", UNIT_NAME, check=False)
    if before["active"] == "active":
        systemctl("restart", UNIT_NAME)
    else:
        systemctl("stop", UNIT_NAME, check=False)


def remote_apply(manifest):
    if sha256(UNIT_TEMPLATE.encode()) != manifest["unit_sha256"]:
        raise ValueError("embedded service unit differs from manifest")
    before = inspect_remote(manifest)
    prior_unit = UNIT_PATH.read_bytes() if before["unit_sha256"] else None
    release = RELEASES / manifest["release"]
    if release.exists():
        verify_release(release, manifest)
    else:
        RELEASES.mkdir(parents=True, exist_ok=True, mode=0o755)
        incoming = RELEASES / ("." + manifest["release"] + f".{os.getpid()}.incoming")
        try:
            extract_release(sys.stdin.buffer, manifest, incoming)
            os.rename(incoming, release)
        finally:
            if incoming.exists():
                import shutil
                shutil.rmtree(incoming)
    account = pwd.getpwnam("pi")
    os.chown(ROOT, account.pw_uid, account.pw_gid)
    if not OWNER_MARKER.exists():
        OWNER_MARKER.write_text("schema=1\n")
        OWNER_MARKER.chmod(0o644)
    ensure_private_token()
    try:
        if before["current"]:
            replace_link(PREVIOUS, before["current"])
        replace_link(CURRENT, f"releases/{manifest['release']}")
        unit_bytes = UNIT_TEMPLATE.encode()
        temporary = UNIT_PATH.with_name(f".{UNIT_PATH.name}.{os.getpid()}.tmp")
        temporary.write_bytes(unit_bytes)
        temporary.chmod(0o644)
        os.replace(temporary, UNIT_PATH)
        systemctl("daemon-reload")
        systemctl("enable", UNIT_NAME)
        systemctl("restart", UNIT_NAME)
        if not health_ok():
            raise RuntimeError("health check failed")
    except Exception as error:
        try:
            restore_deployment(before, prior_unit)
        except Exception as rollback_error:
            raise RuntimeError(
                f"deployment failed and rollback failed: {rollback_error}"
            ) from error
        raise RuntimeError(f"deployment failed; restored previous state: {error}") from error
    return inspect_remote(manifest)


UNIT_TEMPLATE = """[Unit]
Description=Visual wiring guides
After=network.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=/home/pi/visual-guides/current
ExecStart=/usr/bin/python3 /home/pi/visual-guides/current/app.py --bind 0.0.0.0 --port 8791 --static-root /home/pi/visual-guides/current/static --token-file /home/pi/.config/visual-guides/api-token
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("status", "check", "apply"), default="status")
    parser.add_argument("--target", default="pi@vanpi.lan")
    parser.add_argument("--manifest", help=argparse.SUPPRESS)
    parser.add_argument("--remote-status", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--remote-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--remote-apply", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    remote = "apply" if args.remote_apply else "check" if args.remote_check else "status" if args.remote_status else None
    if remote:
        manifest = safe_manifest(args.manifest) if args.manifest else None
        result = remote_apply(manifest) if remote == "apply" else inspect_remote(manifest)
        print(json.dumps(result, sort_keys=True))
        return
    manifest = build_manifest(PROJECT)
    unit = (PROJECT / "visual-guides.service").read_text()
    if unit != UNIT_TEMPLATE or sha256(unit.encode()) != manifest["unit_sha256"]:
        parser.error("visual-guides.service differs from the fixed embedded unit")
    if args.action == "status":
        result = run_ssh(args.target, "status")
    elif args.action == "check":
        result = run_ssh(args.target, "check", manifest=manifest)
    else:
        run_ssh(args.target, "check", manifest=manifest)
        result = run_ssh(args.target, "apply", stdin=make_archive(PROJECT, manifest), manifest=manifest)
    print(json.dumps({"release": manifest["release"], "remote": result}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
