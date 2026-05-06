# ceph_expand

Stage 60 (second half). Runs against the `ceph_nodes` group (all Ceph-participating cluster nodes). Brings a bootstrapped single-host cluster up to a full multi-node CephFS.

## What it does

1. **Load facts** — reads FSID + ceph.conf + admin keyring from the bootstrap node, sets an Ansible fact for downstream tasks.
2. **Authorize cephadm SSH key** — cephadm manages daemons via root SSH from the bootstrap node; authorize its pubkey on every other cluster node.
3. **Add hosts to the orchestrator** — `ceph orch host add <hostname> <storage_ip>` for each non-bootstrap node. Idempotent.
4. **Add OSDs** — from `ceph.osd_devices[hostname]` per node. No auto-discovery; inventory is the source of truth. Pre-wipes each declared device with `wipefs -a` + `sgdisk --zap-all` before `ceph orch daemon add osd` (refuses to wipe if the device is currently mounted) — required at reused-hardware sites where prior partition tables would otherwise cause OSDs to crashloop. Toggle off via `ceph_expand_wipe_osd_disks: false` for re-runs against an already-deployed cluster (wipefs returns "Device or resource busy" when an active OSD holds the disk). The orch-add task's `until` conditions match cephadm's actual idempotent response: `Created osd ...` (success on a new device, but explicitly excluding the false-positive substring within `Created no osd(s) on host ...; already created?`), `already created` in stdout, or `already` / `in use` in stderr. Waits until all OSDs are up.
5. **Create RBD pools** — for each entry in `ceph.pools[]` with `type: rbd`, runs `ceph osd pool create <name> <pg> <pgp>` (idempotent — skips existing), `ceph osd pool application enable <name> rbd` (Ceph refuses I/O on pools without a declared application), and `rbd pool init <name>` (writes rbd_directory metadata so `rbd` commands work). Skipped entirely when no RBD pool is declared.
6. **Create CephFS** — one filesystem per entry in `ceph.pools[]` with `type: cephfs`. `ceph fs volume create` creates data + metadata pools + deploys MDS. (v1 only handles the first cephfs-type entry.)
7. **Mount on every cluster node** — installs `ceph-common`, distributes `ceph.conf` + admin keyring, runs `restorecon -Rv /etc/ceph/` (without it qemu-kvm cannot read `/etc/ceph/ceph.conf` under SELinux and every RBD-backed VM dies on start), creates the mountpoint, writes the fstab entry, mounts. All cluster nodes need shared CephFS access for Pacemaker VM migration.
8. **Define libvirt cephx secret** — creates `ceph.libvirt_cephx_user` (default `client.libvirt`) on the bootstrap node with caps `mon "profile rbd" osd "profile rbd pool=<first rbd pool>"`, then defines a libvirt secret with the fixed `ceph.libvirt_secret_uuid` on every cluster node and sets the value to that user's cephx key. The UUID must be the same on every node — different UUIDs silently break live migration. The role refuses the placeholder all-zeros UUID. Skipped when no RBD pool is declared.
9. **Sanlock Ceph-side chain** — provisions the production storage backend's split-brain protection. Steps: create a 1 GiB `<rbd_pool>/<lockspace>` RBD image; add it to `/etc/ceph/rbdmap` and enable `rbdmap.service` so it auto-maps at boot; `sanlock direct init` on the lockspace device (one-time, from bootstrap node); render `/etc/libvirt/qemu-sanlock.conf` with `auto_disk_leases=0`, `require_lease_for_disks=1`, per-node `host_id` (1-based index from `vpac_nodes` order); drop `sanlock-lockspace.service` systemd oneshot that runs `sanlock client add_lockspace` on every boot (sanlock does NOT persist lockspace registration across reboots); enable + start `sanlock.service` and the oneshot. Skipped when no RBD pool is declared. **After this chain is in place on every cluster node, the operator flips `virtualization_lock_manager` to `sanlock` in inventory and re-runs the virtualization role; libvirtd then refuses to start any VM without a sanlock lease.**
10. **Monitoring deploy** — `ceph orch apply` for prometheus, alertmanager, grafana, and node-exporter. cephadm pulls images from the paths configured by `ceph_bootstrap/monitoring_config.yml` (local registry in air-gapped mode, registry.redhat.io in connected mode).
11. **Verify** — waits for `HEALTH_OK` (or `HEALTH_WARN` if `ceph_expand_require_health_ok: false`), then reports FSID, OSD count, and CephFS names.

## Variables

| Name | Default | Notes |
|---|---|---|
| `ceph_expand_orch_timeout_s` | `300` | max wait for each orchestrator op |
| `ceph_expand_health_timeout_s` | `600` | max wait for final health state |
| `ceph_expand_require_health_ok` | `true` | flip `false` if accepting `HEALTH_WARN` (e.g. minimal lab without enough OSDs for proper redundancy) |
| `ceph_expand_mount_opts` | `"noatime,_netdev"` | fstab options for CephFS |
| `ceph_expand_wipe_osd_disks` | `true` | wipefs + sgdisk OSD devices before add; refuses if mounted |
| `ceph_expand_sanlock_lockspace_name` | `"sanlock-leases"` | RBD image name for the sanlock lockspace; pick once, never rename |
| `ceph_expand_sanlock_lockspace_size_mb` | `1024` | lockspace image size; 1 GiB is enough for hundreds of VMs |

Reads from `group_vars/all.yml`: `vpac_nodes`, `ceph.*` (especially `bootstrap_node`, `osd_devices`, `pools`, `cephfs_mountpoint`).

## Dependencies

- `ceph_bootstrap` (same stage, first half) — sets `ceph_fsid` and the mon is serving.
- `host_baseline` / `networking` / `virtualization` on every cluster node.

## Tags

- `ceph` — everything Ceph (also applies in `ceph_bootstrap`)
- `ceph-expand` — this role specifically
- `ceph-facts`, `ceph-hosts`, `ceph-osds`, `ceph-rbd-pools`, `ceph-fs`, `ceph-mount`, `ceph-libvirt-secret`, `ceph-sanlock`, `ceph-monitoring`, `ceph-verify` — sub-steps

## Sanlock-on-RBD chain

```mermaid
flowchart TD
  rbd[(RBD image:<br/>rbd-vms/sanlock-leases<br/>1 GiB)]
  rbdmap[/etc/ceph/rbdmap<br/>+ rbdmap.service]
  initdev[sanlock direct init<br/>one-time, from bootstrap node]
  qemuconf[/etc/libvirt/qemu-sanlock.conf<br/>per-node host_id<br/>auto_disk_leases=0<br/>require_lease_for_disks=1]
  oneshot[sanlock-lockspace.service<br/>systemd oneshot at boot<br/>sanlock client add_lockspace]
  sanlockd[sanlock.service]
  flip{{Operator flips<br/>virtualization_lock_manager: sanlock<br/>+ re-runs --tags virt-qemu-conf}}
  effect[libvirtd refuses to start a VM<br/>without a sanlock lease]

  rbd --> rbdmap
  rbdmap --> initdev
  initdev --> qemuconf
  qemuconf --> oneshot
  oneshot --> sanlockd
  sanlockd --> flip
  flip --> effect

  classDef operator fill:#fff5d8,stroke:#c80,color:#000
  flip:::operator
```

Until the operator flip the chain is fully provisioned but inactive — `lock_manager: "none"` ignores the lockspace. After the flip, every VM start has to acquire a per-disk lease against the lockspace; if Pacemaker AND fencing both fail simultaneously and try to start a VM on two nodes, the second node's qemu fails to start because it can't acquire the lease.

## Simplifications in v1

- Only the first `type: cephfs` entry in `ceph.pools[]` gets created. Multiple CephFS filesystems = future work.
- Admin keyring is pushed to every node for the CephFS mount. RBD-backed VMs use the scoped `client.libvirt` keyring via the libvirt secret, NOT the admin keyring.
- No pool-level placement rules, PG autoscaling is left at cephadm defaults.
- MDS is deployed by `ceph fs volume create` on the bootstrap node only. For HA MDS, future work layers a second standby MDS on another node.
- Activating sanlock requires one final operator step after this role runs cleanly: flip `virtualization_lock_manager` to `"sanlock"` in inventory and re-run the virtualization role (`ansible-playbook site.yml --tags virt-qemu-conf`). Until that flip, libvirtd ignores the sanlock chain even though it's fully deployed — the chain is configured but inactive.
