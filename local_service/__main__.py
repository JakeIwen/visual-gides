"""Entry point for the foreground worker and its launchd controller."""

import argparse
from pathlib import Path
import sys

from . import control, launchd
from .model import PORT, STATE
from .runtime import serve


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "prepare", "install", "status", "start",
                                           "stop", "restart", "verify", "uninstall"))
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--port", type=int, default=PORT, help=argparse.SUPPRESS)
    parser.add_argument("--state", type=Path, default=STATE, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.action == "serve":
            serve(args.port, args.state)
        elif args.action == "prepare":
            launchd.prepare()
        elif args.action == "status":
            return 0 if control.status() else 1
        elif args.action == "uninstall":
            control.stop(uninstall=True)
        else:
            getattr(control, args.action)()
            if args.action == "install" and args.verify:
                control.verify()
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
