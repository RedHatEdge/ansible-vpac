# ceph_expand

Stage 60 (second half). Runs against the `ceph_nodes` group (all Ceph-participating cluster nodes). Brings a bootstrapped single-host cluster up to a full multi-node CephFS.

## What it does

1. **Load facts** — reads FSID + ceph.conf + admin keyring from the bootstrap node, sets an Ansible fact for downstream tasks.
2. **Authorize cephadm SSH key** — cephadm manages daemons via root SSH from the bootstrap node; authorize its pubkey on every other cluster node.
3. **Add hosts to the orchestrator** — `ceph orch host add <hostname> <storage_ip>` for each non-bootstrap node. Idempotent.
4. **Add OSDs** — from `ceph.osd_devices[hostname]` per node. No auto-discovery; inventory is the source of truth. Pre-wipes each declared device with `wipefs -a` + `sgdisk --zap-all` before `ceph orch daemon add osd` (refuses to wipe if the device is currently mounted) — required at reused-hardware sites where prior partition tables would otherwise cause OSDs to crashloop. Toggle off via `ceph_expand_wipe_osd_disks: false` only when devices are hand-verified empty. Waits until all OSDs are up.
5. **Create RBD pools** — for each entry in `ceph.pools[]` with `type: rbd`, runs `ceph osd pool create <name> <pg> <pgp>` (idempotent — skips existing), `ceph osd pool application enable <name> rbd` (Ceph refuses I/O on pools without a declared application), and `rbd pool init <name>` (writes rbd_directory metadata so `rbd` commands work). Skipped entirely when no RBD pool is declared.
6. **Create CephFS** — one filesystem per entry in `ceph.pools[]` with `type: cephfs`. `ceph fs volume create` creates data + metadata pools + deploys MDS. (v1 only handles the first cephfs-type entry.)
7. **Mount on every cluster node** — installs `ceph-common`, distributes `ceph.conf` + admin keyring, runs `restorecon -Rv /etc/ceph/` (without it qemu-kvm cannot read `/etc/ceph/ceph.conf` under SELinux and every RBD-backed VM dies on start), creates the mountpoint, writes the fstab entry, mounts. All cluster nodes need shared CephFS access for Pacemaker VM migration.
8. **Define libvirt cephx secret** — creates `ceph.libvirt_cephx_user` (default `client.libvirt`) on the bootstrap node with caps `mon "profile rbd" osd "profile rbd pool=<first rbd pool>"`, then defines a libvirt secret with the fixed `ceph.libvirt_secret_uuid` on every cluster node and sets the value to that user's cephx key. The UUID must be the same on every node — different UUIDs silently break live migration. The role refuses the placeholder all-zeros UUID. Skipped when no RBD pool is declared.
9. **Monitoring deploy** — `ceph orch apply` for prometheus, alertmanager, grafana, and node-exporter. cephadm pulls images from the paths configured by `ceph_bootstrap/monitoring_config.yml` (local registry in air-gapped mode, registry.redhat.io in connected mode).
10. **Verify** — waits for `HEALTH_OK` (or `HEALTH_WARN` if `ceph_expand_require_health_ok: false`), then reports FSID, OSD count, and CephFS names.

## Variables

| Name | Default | Notes |
|---|---|---|
| `ceph_expand_orch_timeout_s` | `300` | max wait for each orchestrator op |
| `ceph_expand_health_timeout_s` | `600` | max wait for final health state |
| `ceph_expand_require_health_ok` | `true` | flip `false` if accepting `HEALTH_WARN` (e.g. minimal lab without enough OSDs for proper redundancy) |
| `ceph_expand_mount_opts` | `"noatime,_netdev"` | fstab options for CephFS |
| `ceph_expand_wipe_osd_disks` | `true` | wipefs + sgdisk OSD devices before add; refuses if mounted |

Reads from `group_vars/all.yml`: `vpac_nodes`, `ceph.*` (especially `bootstrap_node`, `osd_devices`, `pools`, `cephfs_mountpoint`).

## Dependencies

- `ceph_bootstrap` (same stage, first half) — sets `ceph_fsid` and the mon is serving.
- `host_baseline` / `networking` / `virtualization` on every cluster node.

## Tags

- `ceph` — everything Ceph (also applies in `ceph_bootstrap`)
- `ceph-expand` — this role specifically
- `ceph-facts`, `ceph-hosts`, `ceph-osds`, `ceph-rbd-pools`, `ceph-fs`, `ceph-mount`, `ceph-libvirt-secret`, `ceph-monitoring`, `ceph-verify` — sub-steps

## Simplifications in v1

- Only the first `type: cephfs` entry in `ceph.pools[]` gets created. Multiple CephFS filesystems = future work.
- Admin keyring is pushed to every node for the CephFS mount. RBD-backed VMs use the scoped `client.libvirt` keyring via the libvirt secret, NOT the admin keyring.
- No pool-level placement rules, PG autoscaling is left at cephadm defaults.
- MDS is deployed by `ceph fs volume create` on the bootstrap node only. For HA MDS, future work layers a second standby MDS on another node.
- The sanlock-on-RBD chain (lockspace image, `qemu-sanlock.conf` with per-node `host_id`, `rbdmap`, `sanlock-lockspace.service` systemd oneshot) lands in a follow-up commit. Without it, RBD-backed VMs work but are not protected against the cluster-wide split-brain failure mode where two nodes start the same VM against the same Ceph image.
