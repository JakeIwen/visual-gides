# Visual Guides & Documentation

Local documentation library and guarded A/C infrared controller, hosted on
[vanpi.lan:8791](http://vanpi.lan:8791). The first guide is
[/guides/air-conditioner.html](http://vanpi.lan:8791/guides/air-conditioner.html).
Python 3.10+ standard library only; no npm build, external fonts, or CDN.

## Files

- `app.py`: HTTP server, static serving, authorization and CLI client. Real CLI
  sends use the same running API admission path as the browser.
- `ac_ir.py`: canonical command contract/catalog, waveform encoding and serialized
  `ir-ctl` transport. Change the catalog here; the UI loads it from the API.
- `hardware_setup.py`: explicit owner-operated GPIO18 boot/udev configuration and
  systemd activation/deactivation. Status and prepare are read-only.
- `deploy.py` and `visual-guides.service`: separate immutable Pi releases and
  guarded systemd install; independent of the dashboard package deployer.
- `static/`: guide index, A/C guide, wiring SVG, command console and
  downloadable reference text. This is the only HTTP-served directory.
- `tests/test_app.py`, `test_deploy.py`, `test_hardware_setup.py`: behavioral and
  deployment-contract tests.

To add a guide, add its page under `static/guides/` and a list entry in
`static/index.html`. Keep hardware-specific APIs explicit. Pages use plain
headings and show reference content immediately, without slogans or promotional
copy.

## Run locally

From this directory:

```sh
python3 app.py --bind 127.0.0.1 --port 8792
```

Open `http://localhost:8792`. Default mode never opens the IR device. Preview a
command without any server or token:

```sh
python3 app.py --preview temp_up
python3 app.py --list-commands
```

## Deploy to the Pi

From this directory on the Mac:

```sh
python3 deploy.py check
python3 deploy.py apply
python3 deploy.py status
```

Default target is `pi@vanpi.lan`. Read-only preflight checks the service, release
ownership and port. Apply transfers allowlisted code and static assets to
`/home/pi/visual-guides/releases/<digest>`, creates a private persistent token,
switches `current`, retains `previous`, enables/restarts only
`visual-guides.service`, and verifies health. A failed cutover restores the
previous release/unit. No boot configuration, GPIO transmission or reboot is
part of deployment. No old release is automatically removed.

The base unit starts in preview mode. An explicitly installed hardware drop-in
survives routine deployments; use the setup controller to deactivate it. The
service listens on port 8791 on IPv4 interfaces, so the docs are readable on LAN
and Tailscale. It does not configure external port forwarding or TLS. Real sends
require a private bearer token and same-origin requests; scripts may omit Origin.
On untrusted networks use SSH forwarding, as shown in the guide.

## Hardware handoff

Read the guide first. GPIO18 (physical12) was unused at inspection; GPIO17 is
already occupied by the alert lamp. The controller rechecks current ownership.
The donor LED's rating and MOSFET pinout must be checked physically. Do not
connect the LED directly to GPIO or omit its series resistor.

On the Pi, as `pi` (the helper invokes sudo only for fixed privileged operations):

```sh
cd /home/pi/visual-guides/current
python3 hardware_setup.py status
python3 hardware_setup.py prepare
python3 hardware_setup.py configure --confirm-wiring
```

Configure backs up the active boot configuration byte-for-byte in private
`~/.local/state/visual-guides/`, adds the managed gpio-ir-tx GPIO18 block, and
installs a driver-specific udev rule for `/dev/van-ac-ir-tx`. It refuses unrelated
pin owners, conflicting overlays and foreign managed-file contents. It does not
load a live overlay or reboot. After safely scheduling a manual reboot:

```sh
python3 hardware_setup.py status
python3 hardware_setup.py activate --confirm-wiring
```

Activation verifies GPIO ownership, LIRC sysfs driver and TX-only features,
installs the hardware service drop-in, and waits for API health. It does not emit
IR. To restore preview-only API operation:

```sh
python3 hardware_setup.py deactivate
```

The boot overlay stays installed. The guide's first-test and token-copy scripts
are reference text, never secrets. The token lives only at
`/home/pi/.config/visual-guides/api-token`, mode 0600, and is never included in
static assets, API responses, command arguments or deployment logs.

## Protocol and API

The exact model is FFRE08L3S15 (zero after FFRE). A supplier lists OEM replacement
5304487535, another identifies that remote as RG15D/E-ELL, and the pinned
[CC0 Frigidaire RG15D capture](https://github.com/Lucaslhm/Flipper-IRDB/blob/d126fb1b6f1e114c52b4a8c19839ea65e3a9c24d/ACs/Frigidaire/Frigidaire_RG15D_AC.ir)
agrees with every command in the catalog. Raw timing comes from the
[measured Kenmore 253.79081 LIRC capture](https://lirc.sourceforge.net/remotes/Kenmore/Kenmore_253_79081).
All sources, confidence limits and electrical references are in the guide.
Physical compatibility with this individual A/C remains untested.

38 kHz, extended NEC, logical address `08 F5`; logical bytes are sent LSB-first.
Legacy LIRC printed `10 AF` is its MSB representation, not the logical address to
pass unchanged to Linux. Temperature up is Linux `necx:0x08f50e`. Each tap sends
one full frame. Held-key repeats, remote sensing and arbitrary raw waveforms are
not exposed.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/health` | Service health |
| `GET /api/ac/status` | Hardware enable state; actual A/C state unknown |
| `GET /api/ac/commands` | Canonical catalog, sources and waveform previews |
| `POST /api/ac/commands/<name>` | Exactly `{"preview":true}` or `{"preview":false}` |

`Content-Type: application/json` is required. Preview needs no token. Actual
transmission requires hardware-enabled service plus `Authorization: Bearer …`.
Commands include temperature up/down, power toggle, cool, fan-only, fan up/down,
auto fan, energy saver, sleep and timer. No absolute temperature or discrete
power on/off is asserted. Timer/sleep behavior needs particular care during
physical validation. Command and transport metadata are generated from the
canonical catalog, not duplicated in the frontend.

Delivery `dry_run` means no signal was sent; `transmitted` means `ir-ctl`
completed, with `confirmed:false` in both cases. No A/C state is guessed. There
is a one-second cooldown after each real attempt, including failures. No retry
or redirect is followed for CLI real sends. A failed/timeout attempt may already
have emitted IR; inspect the A/C before repeating a toggle.

## Verification

From this directory:

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest test_deploy test_hardware_setup -v
node --check static/guide.js
```

Tests exercise real local HTTP calls with an injected recording transport; they
never emit IR. Hardware configure/activate branches are tested with mocked
operating-system boundaries. Browser QA covers desktop and 390px mobile layouts,
preview buttons, token gating, source links and script/style loading. Live
validation checks systemd boot enablement, guide/API health and preview mode.
Actual LED performance, udev matching after reboot and A/C acceptance require
owner bench validation.
