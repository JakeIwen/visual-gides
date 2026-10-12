# Mac hosting

Visual Guides uses `http://127.0.0.1:8792` on the Mac. It serves the checkout
directly, so refreshing a page shows saved HTML/CSS/JS edits. Run `npm run deploy`
after editing to validate, reload Mac Python and deploy to the Pi on port 8791.
For Mac-only Python updates, use `npm run service:restart`.

The controller follows the ProMaster and LionFeather launchd pattern, adapted
into the `local_service` package. There are no runtime third-party dependencies.
The Mac worker is loopback-only and always uses preview mode; it never enables
IR transmission or reads the Pi's token.

The Mac boot service was verified installed and enabled on October 12, 2026.
The combined deployment command demonstrated a graceful exit followed by a new
launchd PID and instance, with HTTP/ownership agreement. Routine edits need
`npm run deploy`, not another installation. Inspect current state with
`npm run service:status`.

## First installation

From this checkout, create the interpreter and prepare the plist:

```sh
python3 -m venv .venv
npm run service:prepare
```

Run installation in a normal Terminal as your normal user:

```sh
npm run service:install
```

Only plist installation and launchctl operations use sudo. The controller never
runs Python or npm as root. Installation also verifies an automatic restart.
Preparing a plist alone does not install or load a boot service.

The system job is `com.jacobr.visual-guides`; its plist is installed as
root:wheel, mode 644, under `/Library/LaunchDaemons/`. It runs `.venv/bin/python`
directly with the checkout as its working directory and the current user as
`UserName`. RunAtLoad and KeepAlive are enabled. Boot performs no dependency
installation, build or shell initialization. The normal macOS sleep and locked
FileVault volume behavior still applies.

## Maintenance

```sh
npm run service:status
npm run service:restart
npm run service:verify
npm run service:stop
npm run service:start
npm run service:uninstall
```

Status is read-only and succeeds only when the owned plist is installed, the
job is enabled and loaded, and launchd's PID matches HTTP health and the private
ownership record. Restart gracefully exits the worker and lets KeepAlive bring
up a new PID and instance. Verify proves that same recovery. Stop disables the
job before unloading it, so it stays stopped until start. Uninstall removes only
the registration; it preserves source, virtual environment and local state.

Plist preparation, stdout/stderr logs and the private owner record live under
ignored `.local/service/`. The owner record is written after binding, mode 600;
its token authorizes `/api/control/stop` and is never returned by health/status.
Controllers refuse foreign port listeners and registrations belonging to another
workspace/user. The health endpoint is `/api/health`.

Inspect launchd directly with:

```sh
launchctl print system/com.jacobr.visual-guides
```

Uninstall before moving this checkout. Reinstall after recreating the interpreter
or changing its configured path. Do not replace a boot daemon with an unattended
manual process or a login agent and describe it as boot registration.
