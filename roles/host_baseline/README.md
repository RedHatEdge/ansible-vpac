# host_baseline

Stage 10. Brings each cluster node to a known, minimal state before any networking, virtualization, or cluster work. Idempotent — safe to re-run.

## What it does

1. **SELinux** — set persistently to `selinux_target_mode` (default `permissive`). Rejects `selinux=0` on the kernel cmdline (which would remove `/sys/fs/selinux` and break Ceph OSD containers). Permissive is the default because qemu-kvm under Enforcing cannot read `/etc/ceph/ceph.conf` without additional context tweaks, breaking every RBD-backed VM on start. Sites with proven SELinux contexts can override to `enforcing`.
2. **Timezone** — set from `site_timezone`
3. **Hostname** — set from the node's `vpac_nodes` entry
4. **/etc/hosts** — write mgmt + storage entries for every node from `vpac_nodes`. Ceph bootstrap and Pacemaker both need hostnames that resolve *without* DNS, since storage networks are L2-only at many sites.
5. **Repo source** — branches on `sources.repo_source`:
   - `rhsm` → `subscription-manager register` with activation key, enable `rhsm_repos`
   - `satellite` → register against the Satellite URL with the same key
   - `local_mirror` → write dnf repo files pointing at `sources.local_mirror_url`; no subscription
6. **Packages** — install the baseline toolset (networking, tracing, cluster-adjacent utilities)
7. **Chrony** — write `/etc/chrony.conf` from `time_sync.ntp_servers`, start `chronyd`, then block on `chronyc waitsync` until chrony reports offset within `time_sync.max_offset_ms` (or fail after `time_sync.sync_timeout_s`). Every downstream stage — Ceph especially — can assume clocks are within NTP tolerance after this runs. When `time_sync.intra_cluster_peer` is true (default), additionally emits a `peer <storage_ip>` line for every other node plus `local stratum 10` + `allow <storage cidr>` — a peer mesh on the storage network so the cluster stays clock-consistent if upstream NTP wobbles. **This `/etc/chrony.conf` is the base layer** — two later roles touch it: `rt_tuning` (stage 50) layers `lock_all` + `sched_priority` + `combinelimit 0` via `blockinfile` on relay hosts, and `ptp_timesync` (stage 40) MASKS system `chronyd` entirely on PTP-having hosts (timemaster spawns its own internal chronyd from `/etc/timemaster.conf` — the `/etc/chrony.conf` layered here is then dormant on those hosts). NTP-follower hosts (no PTP NIC) keep system chronyd running and `ptp_timesync` adds a prefer-PTP-peer block to this same file.
8. **Insights** *(optional)* — when `host_baseline_enable_insights: true` and `sources.repo_source: rhsm`, install `rhc` and run `rhc connect --activation-key --organization` to enrol the host with Red Hat Insights (advisor / vulnerability / compliance data, remote management via rhcd). Reuses the RHSM activation key — no extra credentials. Auto-skipped on `satellite` and `local_mirror` paths since rhc pushes telemetry to `console.redhat.com`.
9. **Firewalld** — start + enable; set the default zone; allow `firewalld_baseline_services` (SSH, cockpit, high-availability) and `firewalld_baseline_ports` (QEMU live migration `49152-49215/tcp`, VNC `5900-5910/tcp`). Downstream roles (ceph_*, pacemaker_*) add their own service-specific ports.
10. **Journald** — persistent storage under `/var/log/journal`, sized to `journald_max_use_mb`

## Variables (with defaults)

| Name | Default | Notes |
|---|---|---|
| `baseline_packages` | see `defaults/main.yml` | extendable by appending in group_vars |
| `baseline_extra_packages` | `[]` | site-specific additions |
| `journald_max_use_mb` | `2048` | persistent journal cap |
| `local_mirror_gpgcheck` | `false` | flip to `true` + set `local_mirror_gpgkey_url` for prod |
| `local_mirror_gpgkey_url` | `""` | required when `local_mirror_gpgcheck: true` |
| `firewalld_default_zone` | `"public"` | |
| `firewalld_baseline_services` | `[ssh, cockpit, high-availability]` | extendable |
| `firewalld_baseline_ports` | `[49152-49215/tcp, 5900-5910/tcp]` | live migration + VNC |
| `selinux_target_mode` | `"permissive"` | flip to `"enforcing"` only if SELinux contexts are proven on the site |
| `host_baseline_skip_repo` | `false` | true = skip RHSM/Satellite/mirror setup (lab DVD installs) |
| `host_baseline_enable_insights` | `false` | true = `rhc connect` to console.redhat.com (RHSM path only) |

Reads from `group_vars/all.yml`: `deployment_mode`, `sources.*`, `rhsm_*`, `vpac_nodes`, `site_domain`, `site_timezone`.

## Subscription model — Simple Content Access (SCA)

This role assumes your Red Hat account has **Simple Content Access** enabled. SCA decouples entitlement from content: register a host with an activation key and every repo your subscriptions cover lights up immediately — no per-host pool attach. SCA has been the default for new Red Hat accounts since ~2022 and is the recommended model for everything Red Hat ships today.

Check by logging into `console.redhat.com` → **Subscriptions** — there is a banner indicating whether SCA is on for your account.

If you are on a **legacy non-SCA account**, set `rhsm_pool` (in `group_vars/all.yml`) to a pool ID that covers RHEL + High Availability + Resilient Storage + NFV + Ceph Storage 7. The role passes it to `community.general.redhat_subscription` and attach happens at register time. Without `rhsm_pool` on a non-SCA account, the host registers but no repos are entitled and the next dnf step fails.

For air-gapped Satellite (`sources.repo_source: satellite`) and local-mirror (`sources.repo_source: local_mirror`) paths, SCA is irrelevant — Satellite handles entitlement on its own and the local mirror serves repo content directly.

## Tags

- `baseline` — everything
- `baseline-selinux`, `baseline-timezone`, `baseline-hostname`, `baseline-hosts`, `baseline-repo`, `baseline-insights`, `baseline-packages`, `baseline-firewall`, `baseline-journald` — individual groups

## Handlers

- `restart journald` — triggered when `/etc/systemd/journald.conf` changes

## Dependencies

None at the role level. Expects `preflight` to have run (but does not enforce it — `site.yml` does).
