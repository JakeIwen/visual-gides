# mwan3-adaptive

Open [the HTML usage guide](docs/guide.html) in a browser for setup commands,
status examples, an interactive connection diagram and troubleshooting. It works
offline and makes no connection to the router.

`mwan3-adaptive` is an experimental companion to mwan3. It is not part of mwan3
or OpenWrt, and is not endorsed or supported by either project. The
stock `mwan3` package dependency is intentional, but the internal policy-chain
layout used by this adapter is not a stable public mwan3 API. Check compatibility
and begin in observation mode after every mwan3 or firewall-backend upgrade.

Version 0.2.0-r1 is a local release candidate awaiting maintainer review and
validation. No package repository or formal release has been published.
Maintainer: Jacob Iwen <lionfeatherdev@gmail.com>.
See [candidate validation](docs/validation.md) for the checks actually performed
and the remaining hardware and IPv6 coverage gaps.

It automatically adjusts how mwan3 distributes **new
connections** across the available uplinks. It learns from actual traffic and
small latency probes; there are no bandwidth subscriptions, external servers,
speed-test downloads, or manually maintained adaptive weights.

This is connection balancing. One TCP connection still uses one uplink. Existing
connections keep their marks when allocations change. A failed uplink can still
break its existing sessions; this package cannot give them a different public IP
without reconnecting.

## Requirements and compatibility

- A working **classic mwan3** installation with its `mwan3_policy_*` iptables
  chains. The adapter follows upstream mwan3 2.12.2's chain format. It supports
  the iptables command API, including the iptables-nft compatibility backend.
  Native nftables forks with different chains are not supported. Unknown or
  changed chain layouts fail closed instead of being modified. Member marks,
  masks and upstream zero-probability quantization are validated before use.
- At least two enabled members **at the same metric** in a selected policy to
  balance concurrently. Higher-metric members remain failover-only. mwan3 owns
  interface eligibility, routes, NAT, firewall marks, sticky rules and tracking.
- Lua 5.1, `flock`, and `iputils-ping` (package dependencies). The stock OpenWrt
  BusyBox `timeout` applet, ubus and jshn are used. IPv6 requires working mwan3
  IPv6 routing and `ip6tables`; this package does not create IPv6 NAT or prefixes.
- Usable interface byte counters. Hardware offload, shared devices or shared
  upstream bottlenecks can make these a poor indicator of independent capacity.
  A policy whose active members report the same L3 device is not adapted;
  status reports `shared interface counters`. Follow mwan3's offloading requirements.

This adapter accepts policy/interface identifiers of 1–15 ASCII letters, digits
or underscores, configured weights of 1–1,000 and metrics of 1–256. It samples
at most 16 active logical interfaces per cycle. Invalid explicitly selected
configuration is an error; unrelated unsupported policies do not prevent a
valid selected policy from operating. Discovery reports unsupported policies as
skipped. These are adapter validation limits, not a promise that every native
mwan3 configuration can be adapted.

No mwan3 code needs patching. The package never writes learned weights to UCI,
restarts mwan3, changes metrics, flushes conntrack, or adds another routing hook.
Installation is disabled and configured for observation mode by default.
Enabling it without a policy list selects
all configured policies with two or more enabled interfaces in one address
family. Use a policy list to restrict its scope, including excluding metered
uplinks by keeping them in another native policy.

## Build

The directory is a normal OpenWrt package. In this checkout, install the packages
feed (which contains mwan3), select `mwan3-adaptive` under **Network → Routing
and Redirection**, and build it using the usual configured OpenWrt build tree or
an SDK matching the router's release. It is architecture-independent Lua/shell;
its dependencies must still match the target firmware.
Enable `rpcd` first if the menu hides mwan3: the current upstream mwan3 feed
requires its RPC module.

From a configured build tree:

```sh
./scripts/feeds update packages
./scripts/feeds install mwan3 iputils-ping
make menuconfig
make package/network/config/mwan3-adaptive/compile V=s
```

For an SDK, copy this directory into `package/mwan3-adaptive` and build
`make package/mwan3-adaptive/compile V=s`. The build system chooses the release's
package format (`.ipk` or `.apk`). Install the resulting package using the
router's package manager. There are currently no published prebuilt packages.
Build from a reviewed source revision, and do not install packages from another
release's feeds.

OpenWrt supports feed-native packages whose source is bundled under `files/`, as
this candidate is, as well as packages built from a separate immutable upstream
archive with a source URL and hash. A separate archive is not required merely
because a package is proposed for a feed; the chosen source model must be
reviewable, reproducible and accepted with the package definition.

## Configure and operate

Keep the existing mwan3 interfaces and routing policies. Native rules must
already direct the desired traffic to those policies. Sticky rules continue to
win; if they pin most new connections, adaptation has correspondingly little
effect.

The configuration is `/etc/config/mwan3-adaptive`. Start in observation mode:

```sh
uci set mwan3-adaptive.main.enabled='1' &&
uci set mwan3-adaptive.main.mode='observe' &&
(uci -q delete mwan3-adaptive.main.policy || true) &&
uci add_list mwan3-adaptive.main.policy='balanced' &&
uci commit mwan3-adaptive &&
/etc/init.d/mwan3-adaptive stop &&
/usr/sbin/mwan3-adaptive check &&
/etc/init.d/mwan3-adaptive enable &&
/etc/init.d/mwan3-adaptive start
```

Replace `balanced` with an existing policy name, or omit the `add_list` command
to discover all policies as described above. Clear the previous policy selection
with the delete command even when switching to automatic discovery. `check`
validates the effective configuration and recognized active policy-chain layout
without probing, changing firewall rules or writing outside the private RAM
runtime directory. It requires the daemon to be stopped.

Generate normal traffic and inspect
`mwan3-adaptive status`. `sampled` and `recommended` are proportional integer
weights, not percentages; compare their shares within each policy/family.
`sampled` is the allocation read at the start of the cycle; after `result=applied`,
the changed policies use the recommended allocations.
The status includes sample uptime, current uptime, measured rates, quality and
skipped policy reasons. `sample_uptime` is the time of the last attempt, not a
guarantee that a stopped daemon is still updating it. `result=error` means no
successful cycle was reported. A failed later-family write is compensated when
possible, but a crash or failed rollback can leave a partial cross-family change
for recovery.

Enable application of the recommendations. The package's restart and reload
paths refuse to start after failed restoration. The explicit `stop && start`
sequence below also makes that gate visible at the command line:

```sh
uci set mwan3-adaptive.main.mode='active' &&
uci commit mwan3-adaptive &&
/etc/init.d/mwan3-adaptive stop &&
/etc/init.d/mwan3-adaptive start
```

`mwan3-adaptive sample` takes a single read-only routing sample, performs its
bounded probes, and prints an initial recommendation while the service is
stopped. It has no previous throughput history. It does not modify routing;
temporary measurements/status are written in RAM. It refuses to run alongside
the daemon. Use `status` while the daemon is running.

`mwan3-adaptive check` validates configuration and compatible active classic
mwan3 chains while the daemon is stopped. It takes a RAM-only routing snapshot
and performs no network probes or firewall writes. Run it after mwan3 or
firewall-backend upgrades and before first activation.

Stop and return the active policy chains to their **current configured mwan3
weights**:

```sh
/etc/init.d/mwan3-adaptive stop &&
/etc/init.d/mwan3-adaptive disable
```

Stop before uninstalling. Shutdown first fences further adaptive writes, then
waits for the worker and restores owned policies, plus pending policies whose
live marks and weights match the journal, under the native mwan3 lock.
Restoration honors current active membership and current configuration; it never
replays a saved firewall snapshot. Successful restoration clears both ownership
ledgers. If restoration fails, its command exits
nonzero and logs the error. `mwan3-adaptive restore` retries after the daemon is
stopped. Native mwan3 continues controlling failover throughout. A native policy
rebuild also replaces adaptive weights with its configured weights, and a running
adaptive controller reconciles on a later sample.

## How decisions work

1. Snapshot the effective UCI configuration and current mwan3 rules. Only
   recognized active policy members are eligible. No fallback tier is promoted.
2. Read netifd's current device, mwan3's online state and byte counters. Run at
   most one small ICMP probe per eligible interface per cycle, rotating through
   that interface's configured literal `track_ip` addresses. `mwan3 use` pins
   each probe to its uplink. No tracking addresses means no latency measurement.
   There are at most 16 sampled interfaces and each probe is bounded to three
   seconds, with probes in parallel. Native mwan3 probes remain separate.
3. Learn smoothed upload/download rates and slowly decaying working capacity
   estimates. Counter resets, device changes and long gaps reset learning.
   Latency growth relative to each target's baseline and probe loss reduce the
   preference for congested links. Missing observations are unknown, not idle.
4. Allocate by estimated spare capacity in the currently dominant traffic
   direction. Keep a total exploration budget across every eligible link so a
   recovered or unused uplink can demonstrate available capacity.
5. Apply only a meaningful change, no more frequently than `update_interval`.
   Under mwan3's own procd lock, recheck the effective configuration, rule
   snapshot, stop fence and sample age. Preflight every selected IPv4 and IPv6
   transaction before writing, then replace only selected policy chains using
   `iptables-restore --noflush`. If a later family fails, attempt to compensate
   prior family writes from checked rollback transactions.

Defaults:

| Option | Default | Meaning |
|---|---:|---|
| `interval` | 10 seconds | Delay between completed sampling cycles; 5–300 |
| `update_interval` | 30 seconds | Minimum time between changes to a policy; at least `interval` |
| `hysteresis` | 0.10 | Minimum total redistribution of probability to apply |
| `exploration` | 0.05 | Total share reserved for learning across eligible links |
| `congestion_ms` | 30 ms | Queueing increase that halves instantaneous latency preference |

Throughput observations are **not a measurement of unused bandwidth**. Learning
requires enough independent new connections and traffic to exercise the links.
A cold start explores equally. One long transfer cannot be moved or split, and
there is no guarantee of summed uplink speed. The algorithm is a tunable
heuristic, not a replacement for congestion control or SQM. It does not override
data caps; put restricted links outside the selected balanced policies.

All runtime files are private and under `/var/run/mwan3-adaptive`; there are no
periodic flash writes. Learning resets on process restart. Ownership is committed
per address-family policy only after a successful firewall transaction. A
`pending` write-ahead allocation covers a kill between the netfilter commit and
that ownership commit. Recovery adopts pending state only when live member marks
and weights still match, and successful static restoration clears both ledgers.

Both family transactions are preflighted before the first write, but the
cross-family operation is not atomic. A process crash or rollback failure can
leave a partial change. The retained journal and guarded restoration handle that
case on restart. Native mwan3 can still rebuild policy chains as links change;
stale snapshots are rejected and retried.

## Verification

Run the public validation entrypoint from this package directory. Select the Lua
interpreter explicitly when more than one is installed:

```sh
LUA=lua5.4 tests/run.sh portable
```

CI runs the portable and Linux modes with Lua 5.1 and 5.4. The pure Lua tests
exercise changing capacities, loss, queueing/recovery, upload asymmetry, unknown
data, device/counter resets, exploration, hysteresis, mark semantics, ownership
recovery, strict chain parsing and current-member restrictions.
The install check executes this Makefile's real install recipe and checks every
file and its mode; it does not claim an SDK dependency build.

On a disposable Linux test system with Lua, Python 3, flock, iproute2 and
iptables/ip6tables installed:

```sh
LUA=lua5.4 tests/run.sh linux
sudo env LUA=lua5.4 unshare --net tests/run.sh kernel
```

The namespace test refuses the host namespace or one with existing interfaces.
It generates real traffic, checks new-flow proportions and an established UDP
session's conntrack mark, and exercises stale snapshots, rejected transactions,
shutdown fencing, static restoration and preservation of unrelated chains.
It tests both family adapters, explicitly skipping IPv6 traffic if the kernel
disables IPv6. The controller test drives the actual CLI with a deterministic
measurement provider. These tests do not establish real-world convergence on
every router, radio link or workload; observe mode is for that validation.
Test availability is not a claim that every revision has passed every check.
Record the exact commands, environment and results when validating a proposed
release.

CI also fetches a pinned mwan3 2.12.2 source revision, verifies fixed hashes,
then validates generated policy chains through legacy and nft iptables APIs in
fresh offline network namespaces. See `tests/upstream-contract.py` and the CI
workflow for the two-phase fetch/run invocation.

Current automated compatibility evidence is bounded to the documented classic
mwan3 2.12.2 chain contract, the legacy and nft iptables command APIs, and the
tested Lua 5.1/5.4 environments. It is not a broader OpenWrt, hardware or native
nftables support claim.

Architecture reference: [upstream mwan3 policy implementation](https://github.com/openwrt/packages/blob/master/net/mwan3/files/lib/mwan3/mwan3.sh).
Only its existing chain contract and locking architecture are reused; the
adaptive controller is implemented here. That internal chain contract can
change without notice; compatibility must be revalidated rather than inferred
from the `mwan3` package name or dependency. License: GPL-2.0-only.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and validation, and
[RELEASING.md](RELEASING.md) for the work still required before a public release
or an OpenWrt packages-feed submission.
