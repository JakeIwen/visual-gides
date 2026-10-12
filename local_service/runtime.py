"""Bind before publishing ownership; supervise only the preview API on the Mac."""

import hmac
from http import HTTPStatus
import json
import os
from pathlib import Path
import secrets
import signal
import tempfile
import threading
from urllib.parse import urlsplit

from ac_ir import COMMANDS
from app import AcIrHandler, AcIrServer, AppConfig
from .model import LABEL, PORT, RECORD_NAME, ROOT, STATE, STOP, Ownership, public_record, read_record


class LocalHandler(AcIrHandler):
    def local_request(self) -> bool:
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in allowed:
            self._error(HTTPStatus.FORBIDDEN, "local Host required")
            return False
        return True

    def do_GET(self) -> None:
        if not self.local_request():
            return
        if urlsplit(self.path).path == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True, **public_record(self.server.ownership)})
        else:
            super().do_GET()

    def do_HEAD(self) -> None:
        if self.local_request():
            super().do_HEAD()

    def do_POST(self) -> None:
        if not self.local_request():
            return
        if urlsplit(self.path).path != STOP:
            super().do_POST()
            return
        supplied = self.headers.get("Authorization", "").encode("latin-1")
        expected = f"Bearer {self.server.ownership['token']}".encode("ascii")
        if not self._origin_allowed() or not hmac.compare_digest(supplied, expected):
            self._error(HTTPStatus.FORBIDDEN, "valid local control token required")
            return
        self.close_connection = True
        self._json(HTTPStatus.ACCEPTED, {"ok": True})
        threading.Thread(target=self.server.shutdown, daemon=True).start()


class LocalServer(AcIrServer):
    ownership: Ownership


def publish_record(state: Path, record: Ownership) -> None:
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.is_symlink() or state.stat().st_uid != os.getuid():
        raise RuntimeError("Service state must be an owned directory")
    state.chmod(0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=state, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(state / RECORD_NAME)
    finally:
        temporary.unlink(missing_ok=True)


def serve(port: int = PORT, state: Path = STATE) -> None:
    config = AppConfig(static_root=ROOT / "static", commands=COMMANDS)
    with LocalServer(("127.0.0.1", port), config, LocalHandler) as server:
        record = Ownership(service=LABEL, root=str(ROOT), uid=os.getuid(), pid=os.getpid(),
                           port=server.server_port, instance=secrets.token_hex(32),
                           token=secrets.token_hex(32))
        server.ownership = record
        publish_record(state, record)
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: threading.Thread(
                target=server.shutdown, daemon=True).start())
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            current = read_record(state)
            if current and current["instance"] == record["instance"]:
                (state / RECORD_NAME).unlink()
