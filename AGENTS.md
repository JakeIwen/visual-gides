# Visual Guides

Standalone public source repository: `JakeIwen/visual-guides`. The local checkout
is `~/dev/visual-guides`; it was extracted from `JakeIwen/scripts` with its four
guide commits preserved. Do not add a second copy to the scripts repository.

## Where things live

- `app.py`: shared HTTP/static server, guarded A/C API and CLI client.
- `ac_ir.py`: canonical RG15D commands, waveform encoding and IR transport.
- `hardware_setup.py`: explicit Pi GPIO18 setup and hardware activation.
- `deploy.py`, `visual-guides.service`: immutable Pi releases and systemd unit.
- `local_service/model.py`: Mac service constants and ownership record contract.
- `local_service/runtime.py`: loopback-only preview server and graceful shutdown.
- `local_service/launchd.py`: plist generation, ownership and launchctl boundary.
- `local_service/control.py`: status, start/stop/restart, install and verification.
- `static/guides/`: source pages; `static/index.html` is the guide directory.
- `static/assets/`: shared styles and guide-specific interactive code.
- `static/reference/`: versioned supporting documents, refreshed with their guide.
- `tests/`, `test_deploy.py`, `test_hardware_setup.py`: behavioral verification.

## Hosting and changes

The Pi serves port 8791 using `/home/pi/visual-guides/current`; the Mac serves
loopback port 8792 using `system/com.jacobr.visual-guides`. See
`docs/local-hosting.md`. Inspect live state before deployment. Never edit an
immutable Pi release, copy runtime state into Git, or expose tokens in output.
Both boot services were verified installed/enabled on October 12, 2026; Mac
KeepAlive recovery was proven through a controlled graceful restart. Reinspect
live state, and do not reinstall a healthy service for routine edits.

The Mac is always preview-only. Keep Pi transmission disabled unless explicitly
requested and hardware verified. GPIO17 belongs to a different service. A send
does not confirm appliance state; preserve the no-retry behavior.

Use the scoped Pi deployer here. Dashboard project links belong to
`scripts/pi/apps/van_dashboard/frontend/src/features/projects/catalog.ts`;
preserve user-added links in its runtime StateStore.

`npm run deploy` is the combined Mac/Pi workflow: check both hosts, run tests,
gracefully restart Mac Python, apply the Pi release, verify Mac supervision.
`npm run deploy:check` is read-only; `npm run deploy:pi` targets only the Pi.
Reuse those paths for routine publication. A healthy unchanged Pi release must
remain a no-op, preserving the previous-release rollback link. Static Mac edits
are visible on refresh; there is no background auto-deployer.

Keep pages as plain reference tools: normal headings, content first, no hero or
marketing copy. Add a directory entry and Source/History links for each guide.
No external runtime fonts, scripts or CDN dependencies. Use `browser_clean` for
UI checks. Do not transmit IR or operate vehicle equipment during testing.

Run `npm test` for Python behavior tests and JavaScript syntax checks. Mac service
tests use temporary state and mocked launchd boundaries, without installing a
job. Keep files under 800 lines and functions under 80 unless a reviewed reason
requires an explicit exception. Reuse existing helpers before adding any.

Commit and push (`c/p`) is the default here: Jacob rates this repo low-risk, so
once a change is finished and tests pass, commit it to `main` and push without
asking. This does not cover deployment or Pi hardware changes.

Keep substantive unfinished work in ignored `.agent/handoffs/`; remove completed
handoffs after promoting durable facts here. Commit extraction/setup separately
from behavioral changes. Never publish private operational context.
