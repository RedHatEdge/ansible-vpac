# ptp_timesync

Stage 40. Configures PTP time on every cluster host. Two execution paths
inside one role, driven by the inventory's `time_sync.mode` and whether
the host has a dedicated PTP NIC:

| `time_sync.mode` | host has PTP NIC | what runs |
|---|---|---|
| `ntp` | * | role end_hosts immediately (host_baseline already configured chrony) |
| `ptp` or `ptp_with_ntp` | yes | **PTP path** — install timemaster, mask system chronyd, render `/etc/timemaster.conf`, drop `ptp_status` writer for virtiofs share, verify GM stability |
| `ptp` or `ptp_with_ntp` | no | **NTP-follower path** — keep system chronyd, prefer the PTP-having peers in `/etc/chrony.conf` so this host follows the cluster's PTP fabric one hop away |

`ptp_with_ntp` adds the inventory's `time_sync.ntp_servers` as `[ntp_server X]`
sections inside `timemaster.conf` so the embedded chronyd has an NTP fallback
when PTP fails. `ptp` mode has no such fallback — chrony's `combinelimit 0`
means PTP wins exclusively while healthy, and on PTP loss chrony is left with
nothing (use `ptp_with_ntp` if that's not acceptable).

## Why timemaster (not bare ptp4l + chronyd)

Two daemons fighting for the system clock is a documented field incident.
`timemaster` (from `linuxptp`) is a single supervisor that spawns ptp4l,
phc2sys, and chronyd with a coordinated config — the only safe pattern when
chrony also needs the same PHC.

The role stops + masks the system `chronyd.service` on PTP hosts so it can't
be re-enabled accidentally; on NTP-follower hosts the system chronyd stays
in charge.

```mermaid
flowchart LR
  subgraph host["PTP host (rt_host)"]
    direction TB
    tm["timemaster.service<br/>(supervisor)"]
    ptp4l[ptp4l<br/>L2 P2P / clientOnly]
    phc2sys[phc2sys<br/>PHC → system clock]
    chrony[chronyd<br/>embedded, system chrony MASKED]
    nic[(PTP NIC<br/>HW timestamping)]
    sys[System clock]
    status["/home/libvirt-local/ptp/<br/>ptp_status (virtiofs)"]
  end
  GM["External grandmaster<br/>GPS-disciplined"] --> nic
  nic --> ptp4l
  ptp4l --> phc2sys
  phc2sys --> sys
  chrony -. lock_all sched_priority 60 .-> sys
  tm --> ptp4l
  tm --> phc2sys
  tm --> chrony
  ptp_status_writer["/usr/local/sbin/ptp_status.sh"] -. polls pmc .-> ptp4l
  ptp_status_writer -. writes .-> status
  status -. virtiofs share .-> relayVM["Relay VM<br/>(SSC600)"]

  classDef external fill:#e8f4ff,stroke:#0066cc
  GM:::external
  relayVM:::external
```

The NTP-follower path is simpler: system `chronyd` stays in charge, with a `blockinfile` on `/etc/chrony.conf` adding `prefer` server lines pointing at every PTP-having peer's `storage_ip`. The follower rides the PTP fabric one hop away.

## What it does (PTP path, in order)

1. **Re-run ptp_isolation** — defense-in-depth: re-asserts the PTP NIC is
   not in a bridge/bond and has no macvtap children, in case anything
   changed since stage 20.
2. **Probe `ethtool -T <ptp_nic>`** — assert PHC index present + hardware-
   transmit/receive timestamping. Fail unless `ptp_timesync_require_hw_timestamping=false`.
3. **Render `/etc/timemaster.conf`** — Power Profile P2P over L2 by default
   (`time_sync.ptp.{transport,delay_mechanism}` overrides). Damping knobs
   `announceReceiptTimeout 5` + `maxStepsRemoved 3` to handle multi-GM BMCA
   without flapping. Embedded chrony block carries the RT relay-host
   tuning (`lock_all`, `sched_priority 60`, `combinelimit 0`).
4. **Stop + mask `chronyd.service`** — timemaster owns chrony from here.
5. **Enable + start `timemaster.service`**.
6. **Install the `chrony.service` → `timemaster.service` alias** (systemd
   symlink) so cephadm's time-sync unit probe passes on pure-PTP hosts —
   see "cephadm compatibility" below. Toggle:
   `ptp_timesync_cephadm_time_sync_alias`.
7. **Drop `ptp_status.sh` + `ptp_status.service`** — polls `pmc` once a
   second and writes `PARENT_DATA_SET / PORT_DATA_SET / TIME_STATUS_NP`
   into `/home/libvirt-local/ptp/ptp_status` for virtiofs share into
   relay VMs. Lets in-VM watchdogs read host PTP health without owning a NIC.
8. **Verify** — `timemaster.service` active, system chronyd masked, ptp4l
   socket present, then 4 samples of `pmc GET PARENT_DATA_SET` 8s apart
   asserting the grandmasterIdentity is constant. Catches BMCA flap when
   multiple grandmasters are reachable on the same domain.

## What it does (NTP-follower path)

1. **`blockinfile` into `/etc/chrony.conf`** — adds `server <peer-storage-ip>
   iburst prefer minpoll 4 maxpoll 6` for every PTP-having peer in
   `vpac_cluster`. Notify chronyd restart.

The follower keeps the inventory's `time_sync.ntp_servers` (templated by
host_baseline) and just adds the prefer-PTP-peers block on top.

## Variables (with defaults)

Almost everything reads from the inventory's `time_sync.*` and
`networking_defaults.*`. Role-internal overrides:

| Name | Default | Notes |
|---|---|---|
| `ptp_timesync_role` | auto: `ptp` if NIC, else `ntp_follower` | force in host_vars to override |
| `ptp_timesync_require_hw_timestamping` | `true` | flip false to deploy on sw-only NICs (lab) |
| `ptp_timesync_ptp4l.{...}` | see `defaults/main.yml` | per-knob ptp4l overrides |
| `ptp_timesync_chrony.{...}` | reads from `rt_chrony.*` | embedded chrony settings |
| `ptp_timesync_status_dir` | `/home/libvirt-local/ptp` | virtiofs share root |
| `ptp_timesync_procbus_phc_nics` | `[]` (reads `time_sync.ptp.procbus_phc_nics`) | process-bus NICs whose PHC is disciplined from the PTP NIC's PHC (one phc2sys instance each) |
| `ptp_timesync_gm_samples` | `4` | GM-stability samples |
| `ptp_timesync_gm_sample_interval` | `8` | seconds between samples (~30s window) |
| `ptp_timesync_cephadm_time_sync_alias` | `true` | install `chrony.service` → `timemaster.service` alias so cephadm's time-sync probe passes on pure-PTP hosts |
| `ptp_timesync_slave_timeout` | `90` (reads `time_sync.ptp.slave_timeout`) | seconds to wait for portState SLAVE |
| `ptp_timesync_max_offset_ns` | `100000` (reads `time_sync.ptp.max_offset_ns`) | worst-case abs offsetFromMaster accepted (~100 µs) |

Reads from `group_vars/all/main.yml`: `time_sync.{mode, ntp_servers, ptp.*}`,
`rt_chrony.{lock_all, sched_priority, combinelimit}`,
`networking_defaults.ptp_nic`, `vpac_nodes[*].{hostname, storage_ip}`.

## The verify gate checks synchronization, not just GM presence

Stage-40 verify asserts three things, because a *stable grandmaster is not the
same as a synchronized node*:

1. **portState reaches SLAVE** — the port actually calibrated and locked.
2. **grandmaster identity is stable** across samples — no BMCA flap between GMs.
3. **offsetFromMaster is bounded AND live** — small (`< ptp_timesync_max_offset_ns`)
   *and* varying across samples (a real servo jitters; a frozen value means it is
   not disciplining the clock).

This exists because of a real hardware failure: a node selected the grandmaster,
held a perfectly stable GM identity, and `peerMeanPathDelay` was healthy — yet it
sat **UNCALIBRATED, never reached SLAVE, and `offsetFromMaster` stayed frozen** at a
default for ~70 h. GM-identity-only verification passed while PTP was dead.

### PTP fabric requirement (switch-side)

The node config can be flawless and PTP still be dead if the switch fabric
stops delivering the messages the sync exchange needs:

- A **two-step** GM sends a `Sync` followed by a separate `Follow_Up` carrying the
  timestamp. **Every switch in the PTP path** must be a P2P transparent clock that
  correctly forwards/regenerates the two-step `Follow_Up`. Without the `Follow_Up`
  the node has Announce, Sync, and healthy Pdelay — and no usable time.
- **Failure signature observed in the field:** stable GM identity + working Pdelay
  (`peerMeanPathDelay` non-zero) + the node stuck UNCALIBRATED with a frozen
  offset. Confirm on the wire and at the port:
  ```bash
  # On the node — expect Sync + Announce + Follow_Up at matched rates;
  # zero Follow_Up = the fabric is not delivering it:
  tcpdump -i <ptp-nic> ether proto 0x88f7

  # ptp4l port/servo state. timemaster runs ptp4l on its own UDS, so the
  # -s socket argument is REQUIRED — bare `pmc -u -b 0` silently returns
  # EMPTY output on these nodes, which reads as dead PTP when it may be fine:
  S=/var/run/timemaster/ptp4l.0.socket
  pmc -s "$S" -u -b 0 "GET PORT_DATA_SET"      # portState: want SLAVE
  pmc -s "$S" -u -b 0 "GET CURRENT_DATA_SET"   # offsetFromMaster: want small AND varying
  pmc -s "$S" -u -b 0 "GET TIME_STATUS_NP"     # master_offset + gmPresent
  ```
- **Remediation, in order of what is actually established:** the recovery lever on
  the affected hardware was **re-initializing the transparent clock's PTP engine**
  (re-issuing its PTP mode configuration). Switching the TC to one-step coincided
  with the original recovery, but a later controlled revert of the same TC to
  two-step synchronized cleanly (correctionField analysis confirmed genuine
  two-step operation on the datapath), so the step-mode *value* is **not** the
  proven root cause — treat one-step as a workaround that removes the `Follow_Up`
  dependency, not as the fix. While diagnosing, also verify the grandmaster is
  GNSS-disciplined and its wall clock is sane: a free-running GM was a confound
  in the original incident. Either way the failure is in the **fabric/GM**, not
  the node, and the verify gate above surfaces it as a clean stage-40 failure
  instead of a false green.

## cephadm compatibility (pure-PTP hosts)

cephadm decides whether time synchronization exists by probing a **fixed
list of systemd unit names** (`chrony.service`, `chronyd.service`,
`systemd-timesyncd.service`, `ntpd.service`, …) for enabled+active — it
never inspects the clock. On a pure-PTP host, chronyd is masked *by
design*, so every name on that list is inactive and cephadm aborts with
`No time synchronization is active` on a node that is PTP-locked to ±ns.
The check fires in **two layers**: `cephadm bootstrap` (`prepare_host`,
bypassable with `--skip-prepare-host`) and the mgr module's `check-host`
during `ceph orch host add` (**no bypass exists**, and it re-runs every
`host_check_interval` for the life of the cluster).

Upstream Ceph resolved this by adding `timemaster.service` to the
recognized list (first shipped in Reef 18.2.4 and in Squid onward). For
cephadm builds that predate it, this role installs the same semantic as a
systemd alias: `/etc/systemd/system/chrony.service` →
`timemaster.service`. The alias is truthful — `chrony.service` is the
Debian unit name for the same chrony service, it does not exist on RHEL
(the RPM ships only `chronyd.service`, which stays masked), and timemaster
supervises the chronyd that disciplines the system clock. `systemctl
is-enabled chrony.service` reports `alias` (rc 0) and `is-active` resolves
to timemaster's state, which is exactly what cephadm's `check_unit`
accepts. Harmless on cephadm builds that already recognize
`timemaster.service`. Disable with
`ptp_timesync_cephadm_time_sync_alias: false` (the alias is then removed
on the next run).

## Coordination with other roles

- **host_baseline (stage 10)** owns the bootstrap chrony config + the
  `time_sync.ntp_servers` list. The PTP path stops + masks system chronyd
  here; NTP-follower path keeps it.
- **networking (stage 20)** brings up the PTP NIC and runs `ptp_isolation`
  at its tail. This role re-runs those checks before arming ptp4l.
- **rt_tuning (stage 50)** owns the kernel-rt + RT cmdline. Its
  `rt_chrony` chrony.conf work is **skipped entirely on timemaster hosts**
  (this role already renders lock_all/sched_priority/combinelimit into
  timemaster.conf from the same `rt_chrony.*` keys, and system chronyd is
  masked — field-found: the old "harmless" blockinfile fired a chronyd
  restart handler that cannot run against a masked unit). It remains
  effective on NTP-mode and NTP-follower hosts that keep system chronyd.
- **Repo rule this role creates:** system chronyd is MASKED on timemaster
  hosts from stage 40 onward. Any component that writes `/etc/chrony.conf`,
  starts chronyd, or notifies a chronyd restart must first handle this case
  (skip on the inventory condition, or detect `systemctl is-enabled chronyd`
  == masked). Read-only `chronyc` commands are safe in both regimes.
- **Corollary — the timemaster-supervised chronyd must stay queryable:**
  vendor relay status tooling (the ABB-shipped `ptp_status.sh`) discovers
  the active ptp4l instance by parsing `chronyc -c sources` and builds the
  pmc socket path from it. Verified from a licensed ABB-built host. Any
  future change that stops chronyd entirely (rather than handing it to
  timemaster) silently kills the relay's PTP health feed. Stage 90
  validates `chronyc -c sources` answers on PTP hosts for this reason.
- **vm_templates (stage 80)** can reference `ptp_timesync_status_dir`
  in a virtiofs filesystem block when a VM needs to read host PTP status.

## Tags

- `ptp` — full role
- `ptp-install` — package install only
- `ptp-check` — HW timestamping + isolation re-check
- `ptp-config`, `ptp-services` — `/etc/timemaster.conf` + service flips
- `ptp-status` — ptp_status writer
- `ptp-follower` — NTP-follower path
- `ptp-verify` — tail-end verification + GM stability sampling

## Handlers

- `restart timemaster` — fires on `timemaster.conf` change
- `restart ptp_status` — fires on script or unit change
- `restart chronyd` — fires only on the NTP-follower path's blockinfile change
