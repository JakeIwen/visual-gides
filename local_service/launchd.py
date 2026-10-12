"""Owned system LaunchDaemon boundary, following the existing local-app pattern."""

import os
from pathlib import Path
import plistlib
import pwd
import re
import stat
import subprocess
import sys

from .model import LABEL, PORT, PYTHON, ROOT, STATE, TARGET

INSTALLED = Path("/Library/LaunchDaemons") / f"{LABEL}.plist"
PAYLOAD = STATE / f"{LABEL}.plist"


def configuration() -> dict[str, object]:
    return {
        "Label": LABEL,
        "UserName": pwd.getpwuid(os.getuid()).pw_name,
        "ProgramArguments": [str(PYTHON), "-m", "local_service", "serve", "--port", str(PORT)],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                                 "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ExitTimeOut": 20,
        "ProcessType": "Background",
        "Umask": 0o077,
        "StandardOutPath": str(STATE / "stdout.log"),
        "StandardErrorPath": str(STATE / "stderr.log"),
    }


def command(program: str, *args: str, privileged: bool = False) -> str:
    interactive = privileged and sys.stdin.isatty()
    prefix = ["/usr/bin/sudo", *([] if interactive else ["-n"])] if privileged else []
    result = subprocess.run([*prefix, program, *args], capture_output=not interactive, text=True)
    if result.returncode:
        raise RuntimeError(f"{program} {' '.join(args)} failed ({result.returncode}): "
                           f"{(result.stderr or result.stdout or 'see Terminal output').strip()}")
    return result.stdout or ""


def assert_owned_configuration(config: dict[str, object]) -> None:
    expected = configuration()
    for key in ("Label", "UserName", "WorkingDirectory", "ProgramArguments"):
        if config.get(key) != expected[key]:
            raise RuntimeError("Refusing a service belonging to another workspace or user")


def installed_configuration() -> dict[str, object] | None:
    try:
        metadata = INSTALLED.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
            or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o644):
        raise RuntimeError("Installed plist must be a regular root:wheel file, mode 644")
    config = plistlib.loads(INSTALLED.read_bytes())
    assert_owned_configuration(config)
    return config


def job() -> dict[str, object]:
    result = subprocess.run(["/bin/launchctl", "print", TARGET], capture_output=True, text=True)
    if result.returncode:
        if "Could not find service" in result.stderr:
            return {"loaded": False, "pid": None}
        raise RuntimeError(f"Cannot inspect {TARGET}: {result.stderr.strip()}")
    match = re.search(r"^\s*pid = (\d+)\s*$", result.stdout, re.M)
    path = re.search(r"^\s*path = (.+)\s*$", result.stdout, re.M)
    if not path or path[1].strip() != str(INSTALLED):
        raise RuntimeError("Loaded job belongs to another registration")
    return {"loaded": True, "pid": int(match[1]) if match else None}


def enabled() -> bool:
    output = command("/bin/launchctl", "print-disabled", "system")
    match = re.search(rf'"{re.escape(LABEL)}"\s*=>\s*(\w+)', output)
    if not match:
        return True
    if match[1] not in {"true", "false", "enabled", "disabled"}:
        raise RuntimeError("Cannot interpret launchd startup state")
    return match[1] in {"false", "enabled"}


def preflight() -> None:
    if sys.platform != "darwin" or os.getuid() == 0:
        raise RuntimeError("Run the service controller as your normal user on macOS")
    if not PYTHON.is_file() or not os.access(PYTHON, os.X_OK):
        raise RuntimeError("Create the project interpreter first: python3 -m venv .venv")
    command("/bin/launchctl", "print", "system")
    config = installed_configuration()
    if job()["loaded"] and not config:
        raise RuntimeError("Loaded service has no owned plist; refusing to change it")
    if not (ROOT / "static" / "index.html").is_file():
        raise RuntimeError("Guide index is missing")


def prepare() -> None:
    preflight()
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.is_symlink() or STATE.stat().st_uid != os.getuid():
        raise RuntimeError("Service state must be an owned directory")
    STATE.chmod(0o700)
    PAYLOAD.write_bytes(plistlib.dumps(configuration(), sort_keys=False))
    PAYLOAD.chmod(0o600)
    command("/usr/bin/plutil", "-lint", str(PAYLOAD))
    for name in ("stdout.log", "stderr.log"):
        path = STATE / name
        path.touch(mode=0o600, exist_ok=True)
    print(f"Prepared {PAYLOAD}; boot registration is not installed by prepare.")
