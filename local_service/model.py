"""Shared local-service paths and private ownership contract."""

import json
import os
from pathlib import Path
import re
import stat
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local" / "service"
LABEL = "com.jacobr.visual-guides"
TARGET = f"system/{LABEL}"
PORT = 8792
URL = f"http://127.0.0.1:{PORT}"
HEALTH = "/api/health"
STOP = "/api/control/stop"
RECORD_NAME = "owner.json"
PYTHON = ROOT / ".venv" / "bin" / "python"


class Ownership(TypedDict):
    service: str
    root: str
    uid: int
    pid: int
    port: int
    instance: str
    token: str


def read_record(state: Path = STATE) -> Ownership | None:
    path = state / RECORD_NAME
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077):
        raise RuntimeError("Ownership record must be a private, owned regular file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != set(Ownership.__annotations__):
        raise RuntimeError("Invalid service ownership record")
    for key in ("uid", "pid", "port"):
        if type(value[key]) is not int or value[key] < 0:
            raise RuntimeError("Invalid service ownership number")
    if (value["service"] != LABEL or value["root"] != str(ROOT)
            or value["uid"] != os.getuid() or value["pid"] == 0
            or not 1 <= value["port"] <= 65535):
        raise RuntimeError("Service ownership belongs to another workspace or user")
    for key in ("instance", "token"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9]{64}", value[key]):
            raise RuntimeError("Invalid service ownership credential")
    return value


def public_record(record: Ownership) -> dict[str, object]:
    return {key: value for key, value in record.items() if key != "token"}
