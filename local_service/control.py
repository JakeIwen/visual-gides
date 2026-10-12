"""Verify HTTP/ownership/launchd agreement before controlling an owned service."""

import http.client
import json
import socket
import time

from . import launchd
from .model import HEALTH, PORT, STATE, STOP, TARGET, URL, Ownership, public_record, read_record


def health() -> dict[str, object] | None:
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
    try:
        connection.request("GET", HEALTH)
        response = connection.getresponse()
        data = json.loads(response.read(65536))
        return data if response.status == 200 and isinstance(data, dict) else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def owned_runtime() -> Ownership | None:
    live = health()
    if live is None:
        return None
    record = read_record()
    if (not record or record["port"] != PORT or live.get("ok") is not True
            or any(live.get(key) != value for key, value in public_record(record).items())):
        raise RuntimeError("HTTP and private ownership records do not match; nothing was stopped")
    return record


def assert_port_available_or_owned() -> Ownership | None:
    record = owned_runtime()
    if record:
        return record
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=2):
            raise RuntimeError(f"Port {PORT} has an unrecognized listener; nothing was stopped")
    except ConnectionRefusedError:
        return None


def stop_owned() -> Ownership:
    record = owned_runtime()
    if not record:
        raise RuntimeError("No owned local service is responding")
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=3)
    try:
        connection.request("POST", STOP, body=b"", headers={
            "Authorization": f"Bearer {record['token']}"})
        response = connection.getresponse()
        response.read()
        if response.status != 202:
            raise RuntimeError(f"Graceful shutdown rejected ({response.status})")
    finally:
        connection.close()
    return record


def wait_ready(previous: Ownership | None = None, timeout: float = 35) -> Ownership:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = owned_runtime()
        job = launchd.job()
        if (record and job["loaded"] and job["pid"] == record["pid"]
                and (previous is None or (record["pid"] != previous["pid"]
                     and record["instance"] != previous["instance"]))):
            return record
        time.sleep(0.25)
    raise RuntimeError(f"Service did not become ready; inspect {STATE / 'stderr.log'}")


def wait_stopped(previous: Ownership) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        live = health()
        if not live or live.get("instance") != previous["instance"]:
            return
        time.sleep(0.2)
    raise RuntimeError("Previous service process did not stop")


def status() -> bool:
    config = launchd.installed_configuration()
    job = launchd.job()
    record = owned_runtime()
    enabled = launchd.enabled()
    supervised = bool(config and config.get("RunAtLoad") is True
                      and config.get("KeepAlive") is True and enabled and job["loaded"]
                      and record and job["pid"] == record["pid"])
    print(json.dumps({"service": TARGET, "installed": config is not None,
                      "startup_enabled": enabled, "job": job, "supervised": supervised,
                      "url": URL, "process": public_record(record) if record else None}, indent=2))
    return supervised


def install() -> None:
    launchd.prepare()
    previous = assert_port_available_or_owned()
    # Obtain privilege before interrupting an existing owned process.
    launchd.command("/usr/bin/install", "-o", "root", "-g", "wheel", "-m", "644",
                    str(launchd.PAYLOAD), str(launchd.INSTALLED), privileged=True)
    launchd.command("/bin/launchctl", "enable", TARGET, privileged=True)
    if launchd.job()["loaded"]:
        launchd.command("/bin/launchctl", "bootout", TARGET, privileged=True)
    elif previous:
        stop_owned()
    if previous:
        wait_stopped(previous)
    launchd.command("/bin/launchctl", "bootstrap", "system", str(launchd.INSTALLED), privileged=True)
    record = wait_ready()
    print(f"Installed {TARGET}; PID {record['pid']}; {URL}")


def start() -> None:
    launchd.preflight()
    if not launchd.installed_configuration():
        raise RuntimeError("Run npm run service:install first")
    previous = assert_port_available_or_owned()
    launchd.command("/bin/launchctl", "enable", TARGET, privileged=True)
    if not launchd.job()["loaded"]:
        if previous:
            stop_owned()
            wait_stopped(previous)
        launchd.command("/bin/launchctl", "bootstrap", "system",
                        str(launchd.INSTALLED), privileged=True)
    elif previous is None:
        launchd.command("/bin/launchctl", "kickstart", TARGET, privileged=True)
    record = wait_ready()
    print(f"Running {TARGET}; PID {record['pid']}; {URL}")


def stop(uninstall: bool = False) -> None:
    launchd.preflight()
    if not launchd.installed_configuration():
        raise RuntimeError("Service is not installed")
    previous = assert_port_available_or_owned()
    launchd.command("/bin/launchctl", "disable", TARGET, privileged=True)
    if launchd.job()["loaded"]:
        launchd.command("/bin/launchctl", "bootout", TARGET, privileged=True)
    elif previous:
        stop_owned()
    if previous:
        wait_stopped(previous)
    if uninstall:
        launchd.command("/bin/rm", "--", str(launchd.INSTALLED), privileged=True)
    print("Registration removed; project retained." if uninstall else
          "Stopped; automatic startup is disabled until service:start.")


def restart() -> None:
    launchd.preflight()
    if not status():
        raise RuntimeError("Service must be installed, enabled and healthy before restart")
    before = stop_owned()
    after = wait_ready(previous=before)
    print(f"Automatic restart verified: PID {before['pid']} -> {after['pid']}; {URL}")


def verify() -> None:
    restart()
    if not status():
        raise RuntimeError("Service lost supervision after restart")
