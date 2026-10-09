#!/usr/bin/env python3
"""Safely configure and activate the optional GPIO18 IR transmitter.

Run this controller as user ``pi``. It uses sudo only for fixed boot, udev and
systemd paths. It never loads a runtime overlay, reboots, or transmits IR.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile
import time
import urllib.request


GPIO = 18
BOOT_CANDIDATES = (Path("/boot/firmware/config.txt"), Path("/boot/config.txt"))
BACKUP_DIR = Path("/home/pi/.local/state/visual-guides")
RULE_PATH = Path("/etc/udev/rules.d/70-visual-guides-ir-tx.rules")
DROPIN_DIR = Path("/etc/systemd/system/visual-guides.service.d")
DROPIN_PATH = DROPIN_DIR / "hardware.conf"
DEVICE = Path("/dev/van-ac-ir-tx")
SERVICE = "visual-guides.service"
BEGIN = "# BEGIN visual-guides gpio-ir-tx"
END = "# END visual-guides gpio-ir-tx"
OVERLAY = "dtoverlay=gpio-ir-tx,gpio_pin=18"
OWN_CONSUMERS = {"gpio-ir-tx", "gpio-ir-transmitter@12"}
BLOCK = f"{BEGIN}\n[all]\n{OVERLAY}\n{END}\n"
UDEV_RULE = (
    '# visual-guides: stable name for the gpio-ir-tx LIRC transmitter\n'
    'SUBSYSTEM=="lirc", KERNEL=="lirc[0-9]*", DRIVERS=="gpio-ir-tx", '
    'SYMLINK+="van-ac-ir-tx", GROUP="gpio", MODE="0660"\n'
)
EXEC_START = (
    "/usr/bin/python3 /home/pi/visual-guides/current/app.py --bind 0.0.0.0 "
    "--port 8791 --static-root /home/pi/visual-guides/current/static "
    "--token-file /home/pi/.config/visual-guides/api-token"
)
DROPIN = f"[Service]\nExecStart=\nExecStart={EXEC_START} --enable-hardware --device {DEVICE}\n"


class SetupError(RuntimeError):
    pass


def run(args, *, sudo=False, check=True, input_text=None):
    command = (["sudo", "-n"] if sudo else []) + [str(part) for part in args]
    try:
        return subprocess.run(
            command, input=input_text, text=True, capture_output=True,
            check=check, timeout=20,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        raise SetupError(f"command failed: {command[0]}: {detail}") from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SetupError(f"command failed: {command[0]}: {error}") from error


def boot_config(candidates=BOOT_CANDIDATES):
    firmware, legacy = candidates
    if firmware.is_file():
        if legacy.is_file():
            stub = legacy.read_text(errors="replace")
            if "moved to /boot/firmware/config.txt" not in stub:
                raise SetupError("both boot configs exist and the legacy path is not the Debian redirect stub")
        return firmware
    if legacy.is_file():
        return legacy
    raise SetupError("Raspberry Pi boot config is missing")


def overlay_state(text):
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1:
        raise SetupError("boot config has malformed visual-guides markers")
    managed = BLOCK.rstrip() in text
    if text.count(BEGIN) and not managed:
        raise SetupError("boot config has modified visual-guides managed block")
    active = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and line.startswith("dtoverlay=gpio-ir-tx"):
            active.append(line)
    unsupported = [line for line in active if line != OVERLAY]
    if unsupported:
        raise SetupError("conflicting gpio-ir-tx overlay: " + ", ".join(unsupported))
    if len(active) > 1:
        raise SetupError("duplicate gpio-ir-tx overlays in boot config")
    return {"configured": active == [OVERLAY], "managed": managed}


def gpio_consumer(output):
    for line in output.splitlines():
        if re.match(r"\s*line\s+18:", line):
            quoted = re.findall(r'"([^"]*)"', line)
            consumer = quoted[1] if len(quoted) > 1 else ""
            used = "[used" in line
            return consumer if used else None
    raise SetupError("gpioinfo did not report GPIO18")


def pin_state(output):
    match = re.search(r"^18:\s+([^|]+)", output.strip())
    if not match:
        raise SetupError("pinctrl did not report GPIO18")
    return match.group(1).strip()


def inspect_pin(*, allow_own):
    info = run(["/usr/bin/gpioinfo", "gpiochip0"]).stdout
    consumer = gpio_consumer(info)
    if consumer and not (allow_own and consumer in OWN_CONSUMERS):
        raise SetupError(f"GPIO18 is occupied by {consumer!r}")
    pin = pin_state(run(["/usr/bin/pinctrl", "get", str(GPIO)]).stdout)
    if consumer is None and not pin.startswith("ip"):
        raise SetupError(f"GPIO18 pinctrl state is not an unused input: {pin}")
    return {"consumer": consumer, "pinctrl": pin}


def fixed_file_state(path, expected):
    if not path.exists():
        return "absent"
    if path.is_symlink() or not path.is_file():
        raise SetupError(f"refusing non-regular managed path: {path}")
    return "managed" if path.read_text() == expected else "foreign"


def driver_for_device(device):
    if not device.is_symlink():
        if device.exists():
            raise SetupError(f"{device} is not the managed symlink")
        return None
    try:
        target = device.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise SetupError(f"cannot resolve managed LIRC device: {error}") from error
    if not re.fullmatch(r"/dev/lirc[0-9]+", str(target)):
        raise SetupError(f"unexpected LIRC target: {target}")
    node = Path("/sys/class/lirc") / target.name / "device"
    try:
        resolved = node.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise SetupError(f"cannot resolve LIRC sysfs device: {error}") from error
    for candidate in (resolved, *resolved.parents):
        driver = candidate / "driver"
        if driver.is_symlink():
            name = driver.resolve().name
            if name == "gpio-ir-tx":
                return name
    raise SetupError("LIRC device is not parented by the gpio-ir-tx driver")


def verify_transmit_device():
    driver = driver_for_device(DEVICE)
    if driver is None:
        raise SetupError(f"{DEVICE} is absent; reboot after configure")
    result = run(["/usr/bin/ir-ctl", "--features", "--device", str(DEVICE)])
    features = result.stdout.lower()
    if "can send raw ir" not in features:
        raise SetupError("LIRC device does not advertise raw IR transmission")
    if "can receive raw ir" in features:
        raise SetupError("refusing a receive-capable LIRC device; expected TX-only gpio-ir-tx")
    return {"driver": driver, "features": "raw-ir transmit only"}


def write_fixed(path, content, mode):
    with tempfile.NamedTemporaryFile("w", delete=False, prefix="visual-guides-") as stream:
        stream.write(content)
        temporary = Path(stream.name)
    try:
        run(["/usr/bin/install", "-o", "root", "-g", "root", "-m", mode, temporary, path], sudo=True)
    finally:
        temporary.unlink(missing_ok=True)


def require_pi_owner():
    if pwd.getpwuid(os.geteuid()).pw_name != "pi":
        raise SetupError("run hardware setup as user pi, not through sudo")


def save_private_backup(content):
    if BACKUP_DIR.exists() and (BACKUP_DIR.is_symlink() or not BACKUP_DIR.is_dir()):
        raise SetupError(f"refusing unsafe backup directory: {BACKUP_DIR}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    BACKUP_DIR.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = BACKUP_DIR / f"boot-config-{stamp}.bak"
    try:
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except OSError as error:
        raise SetupError(f"could not save private boot-config backup: {error}") from error
    return backup


def wait_api(hardware_enabled, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8791/api/ac/status", timeout=1) as response:
                body = json.loads(response.read())
                if response.status == 200 and body.get("hardware_enabled") is hardware_enabled:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    return False


def configure(confirm):
    require_pi_owner()
    if not confirm:
        raise SetupError("configure requires --confirm-wiring after checking GPIO18 / physical pin 12")
    config = boot_config()
    original = config.read_text()
    state = overlay_state(original)
    if state["configured"] and not state["managed"]:
        raise SetupError("gpio-ir-tx is already configured outside the managed block; leave it unchanged or remove it manually after review")
    inspect_pin(allow_own=state["configured"])
    rule = fixed_file_state(RULE_PATH, UDEV_RULE)
    if rule == "foreign":
        raise SetupError(f"refusing foreign udev rule: {RULE_PATH}")
    if fixed_file_state(DROPIN_PATH, DROPIN) == "foreign":
        raise SetupError(f"refusing foreign service drop-in: {DROPIN_PATH}")
    changed = []
    if not state["managed"]:
        backup = save_private_backup(config.read_bytes())
        updated = original.rstrip() + "\n\n" + BLOCK
        write_fixed(config, updated, "0644")
        changed.extend([str(config), str(backup)])
    if rule == "absent":
        write_fixed(RULE_PATH, UDEV_RULE, "0644")
        run(["/usr/bin/udevadm", "control", "--reload-rules"], sudo=True)
        changed.append(str(RULE_PATH))
    return {"configured": True, "changed": changed, "next": "reboot manually, then run activate --confirm-wiring"}


def activate(confirm):
    require_pi_owner()
    if not confirm:
        raise SetupError("activate requires --confirm-wiring after checking GPIO18 / physical pin 12")
    state = overlay_state(boot_config().read_text())
    if not state["configured"]:
        raise SetupError("gpio-ir-tx is not configured in the boot config")
    pin = inspect_pin(allow_own=True)
    if pin["consumer"] not in OWN_CONSUMERS:
        raise SetupError("gpio-ir-tx does not own GPIO18; reboot after configure")
    device = verify_transmit_device()
    current = fixed_file_state(DROPIN_PATH, DROPIN)
    if current == "foreign":
        raise SetupError(f"refusing foreign service drop-in: {DROPIN_PATH}")
    installed = current == "absent"
    if installed:
        run(["/usr/bin/install", "-d", "-o", "root", "-g", "root", "-m", "0755", DROPIN_DIR], sudo=True)
        write_fixed(DROPIN_PATH, DROPIN, "0644")
    try:
        run(["/usr/bin/systemctl", "daemon-reload"], sudo=True)
        run(["/usr/bin/systemctl", "restart", SERVICE], sudo=True)
        if not wait_api(True):
            raise SetupError("hardware service did not become healthy")
    except SetupError as error:
        if installed:
            run(["/usr/bin/rm", "--", DROPIN_PATH], sudo=True, check=False)
            run(["/usr/bin/systemctl", "daemon-reload"], sudo=True, check=False)
            run(["/usr/bin/systemctl", "restart", SERVICE], sudo=True, check=False)
            wait_api(False)
        raise SetupError(f"{error}; preview mode was restored when newly enabled") from error
    return {"activated": True, "device": device}


def deactivate():
    require_pi_owner()
    current = fixed_file_state(DROPIN_PATH, DROPIN)
    if current == "foreign":
        raise SetupError(f"refusing foreign service drop-in: {DROPIN_PATH}")
    if current == "managed":
        run(["/usr/bin/rm", "--", DROPIN_PATH], sudo=True)
    run(["/usr/bin/systemctl", "daemon-reload"], sudo=True)
    run(["/usr/bin/systemctl", "restart", SERVICE], sudo=True)
    if not wait_api(False):
        raise SetupError("preview service did not become healthy after deactivation")
    return {"activated": False, "boot_overlay_retained": True}


def status():
    config = boot_config()
    overlay = overlay_state(config.read_text())
    try:
        pin = inspect_pin(allow_own=True)
    except SetupError as error:
        pin = {"error": str(error)}
    try:
        device = {"driver": driver_for_device(DEVICE)}
    except SetupError as error:
        device = {"error": str(error)}
    service = run(["/usr/bin/systemctl", "is-active", SERVICE], check=False).stdout.strip()
    return {
        "boot_config": str(config), "overlay": overlay, "gpio18": pin,
        "udev_rule": fixed_file_state(RULE_PATH, UDEV_RULE),
        "device": device, "hardware_dropin": fixed_file_state(DROPIN_PATH, DROPIN),
        "service": service,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", nargs="?",
        choices=("status", "prepare", "configure", "activate", "deactivate", "enable", "disable"),
        default="status",
    )
    parser.add_argument("--confirm-wiring", action="store_true")
    args = parser.parse_args(argv)
    action = {"enable": "activate", "disable": "deactivate"}.get(args.action, args.action)
    try:
        if action in ("status", "prepare"):
            result = status()
            if action == "prepare":
                overlay = result["overlay"]
                result["ready_to_configure"] = not overlay["managed"] and "error" not in result["gpio18"]
        elif action == "configure":
            result = configure(args.confirm_wiring)
        elif action == "activate":
            result = activate(args.confirm_wiring)
        else:
            result = deactivate()
    except SetupError as error:
        parser.exit(1, f"hardware setup refused: {error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
