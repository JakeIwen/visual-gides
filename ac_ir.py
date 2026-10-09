"""A/C infrared command definitions and the guarded ``ir-ctl`` transport."""

from __future__ import annotations

import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence


DEFAULT_DEVICE = Path("/dev/van-ac-ir-tx")
DEFAULT_CARRIER_HZ = 38_000
IR_CTL = Path("/usr/bin/ir-ctl")
MAX_PULSES = 1_000
MAX_DURATION_US = 1_000_000


@dataclass(frozen=True)
class Command:
    """One reviewed, named IR waveform.

    Durations alternate mark, space, mark, space and must end in a mark. The
    final quiet period is supplied by the caller or the receiving appliance.
    """

    name: str
    label: str
    encoding: str
    carrier_hz: int
    durations_us: tuple[int, ...]
    source: str
    linux_scancode: str | None = None
    source_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError("command name must contain letters, digits, and underscores")
        if not (20_000 <= self.carrier_hz <= 60_000):
            raise ValueError("carrier frequency is outside the supported range")
        if not self.durations_us or len(self.durations_us) % 2 == 0:
            raise ValueError("durations must contain alternating mark/space values ending in mark")
        if len(self.durations_us) > MAX_PULSES:
            raise ValueError("waveform has too many durations")
        if any(not 0 < value <= MAX_DURATION_US for value in self.durations_us):
            raise ValueError("waveform duration is outside the supported range")

    def preview(self) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "encoding": self.encoding,
            "carrier_hz": self.carrier_hz,
            "pulse_count": len(self.durations_us),
            "durations_us": list(self.durations_us),
            "source": self.source,
            "linux_scancode": self.linux_scancode,
            "source_urls": list(self.source_urls),
        }


COMPATIBILITY_SOURCE = (
    "Kenmore 253.79081 measured LIRC timings; Frigidaire RG15D command profile "
    "from Flipper-IRDB revision d126fb1; exact FFRE08L3S15 physical validation pending"
)
NEC_ENCODING = "NEC extended, raw waveform, logical address 0x08f5"
SOURCE_URLS = (
    "https://lirc.sourceforge.net/remotes/Kenmore/Kenmore_253_79081",
    "https://github.com/Lucaslhm/Flipper-IRDB/blob/"
    "d126fb1b6f1e114c52b4a8c19839ea65e3a9c24d/ACs/Frigidaire/"
    "Frigidaire_RG15D_AC.ir",
)


def _nec_extended_waveform(command_byte: int) -> tuple[int, ...]:
    """Build the measured profile's raw waveform in NEC wire-bit order."""
    if not 0 <= command_byte <= 0xFF:
        raise ValueError("NEC command byte must fit in one byte")
    wire_bytes = (0x08, 0xF5, command_byte, command_byte ^ 0xFF)
    durations = [9159, 4455]
    for byte in wire_bytes:
        for bit_index in range(8):
            durations.extend((639, 1615 if byte & (1 << bit_index) else 486))
    durations.append(637)
    return tuple(durations)


def _compatibility_command(name: str, label: str, command_byte: int) -> Command:
    return Command(
        name=name,
        label=label,
        encoding=NEC_ENCODING,
        carrier_hz=DEFAULT_CARRIER_HZ,
        durations_us=_nec_extended_waveform(command_byte),
        source=COMPATIBILITY_SOURCE,
        linux_scancode=f"necx:0x08f5{command_byte:02x}",
        source_urls=SOURCE_URLS,
    )


# These byte values are converted from the legacy LIRC profile's MSB display
# into the logical NEC bytes emitted LSB-first on the wire. They are a useful
# starter profile, but the exact FFRE08L3S15 receiver has not yet verified them.
COMMANDS: Mapping[str, Command] = MappingProxyType(
    {
        command.name: command
        for command in (
            _compatibility_command("power_toggle", "Power", 0x11),
            _compatibility_command("temp_up", "Temperature up", 0x0E),
            _compatibility_command("temp_down", "Temperature down", 0x0D),
            _compatibility_command("mode_cool", "Cool mode / start", 0x09),
            _compatibility_command("energy_saver", "Energy saver", 0x02),
            _compatibility_command("mode_fan", "Fan-only mode", 0x07),
            _compatibility_command("fan_auto", "Auto fan", 0x0F),
            _compatibility_command("fan_down", "Fan down", 0x04),
            _compatibility_command("fan_up", "Fan up", 0x01),
            _compatibility_command("sleep", "Sleep", 0x00),
            _compatibility_command("timer", "Timer", 0x06),
        )
    }
)


class Transport(Protocol):
    def send(self, command: Command) -> None:
        """Send one command or raise an exception."""


class IrCtlTransport:
    """Serialize waveform delivery through the kernel's configured LIRC TX."""

    def __init__(
        self,
        device: Path = DEFAULT_DEVICE,
        executable: Path = IR_CTL,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.device = device
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def send(self, command: Command) -> None:
        waveform = format_mode2(command.durations_us)
        with self._lock, tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", prefix="ac-ir-", suffix=".mode2"
        ) as handle:
            handle.write(waveform)
            handle.flush()
            subprocess.run(
                [
                    str(self.executable),
                    f"--device={self.device}",
                    f"--carrier={command.carrier_hz}",
                    f"--send={handle.name}",
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
            )


def format_mode2(durations_us: Sequence[int]) -> str:
    """Return an ir-ctl mode2 file from validated alternating durations."""
    if not durations_us or len(durations_us) % 2 == 0:
        raise ValueError("durations must end with a pulse")
    if len(durations_us) > MAX_PULSES:
        raise ValueError("waveform has too many durations")
    lines = []
    for index, duration in enumerate(durations_us):
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise TypeError("waveform durations must be integers")
        if not 0 < duration <= MAX_DURATION_US:
            raise ValueError("waveform duration is outside the supported range")
        kind = "pulse" if index % 2 == 0 else "space"
        lines.append(f"{kind} {duration}")
    return "\n".join(lines) + "\n"
