# ceph_expand

Stage 60 (second half). Runs against the `ceph_nodes` group (all Ceph-participating cluster nodes). Brings a bootstrapped single-host cluster up to a full multi-node CephFS.

## What it does

1. **Load facts** — reads FSID + ceph.conf + admin keyring from the bootstrap node, sets an Ansible fact for downstream tasks.
2. **Authorize cephadm SSH key** — cephadm manages daemons via root SSH from the bootstrap node; authorize its pubkey on every other cluster node.
3. **Add hosts to the orchestrator** — `ceph orch host add <hostname> <storage_ip>` for each non-bootstrap node. Idempotent.
4. **Add OSDs** — from `ceph.osd_devices[hostname]` per node. No auto-discovery; inventory is the source of truth. Waits until all OSDs are up.
5. **Create CephFS** — one filesystem per entry in `ceph.pools[]` with `type: cephfs`. `ceph fs volume create` creates data + metadata pools + deploys MDS. (v1 only handles the first cephfs-type entry; RBD support comes later.)
6. **Mount on every cluster node** — installs `ceph-common`, distributes `ceph.conf` + admin keyring, creates the mountpoint, writes the fstab entry, mounts. All cluster nodes need shared CephFS access for Pacemaker VM migration.
7. **Verify** — waits for `HEALTH_OK` (or `HEALTH_WARN` if `ceph_expand_require_health_ok: false`).

## Variables

| Name | Default | Notes |
|---|---|---|
| `ceph_expand_orch_timeout_s` | `300` | max wait for each orchestrator op |
| `ceph_expand_health_timeout_s` | `600` | max wait for final health state |
| `ceph_expand_require_health_ok` | `true` | flip `false` if accepting `HEALTH_WARN` (e.g. minimal lab without enough OSDs for proper redundancy) |
| `ceph_expand_mount_opts` | `"noatime,_netdev"` | fstab options for CephFS |

Reads from `group_vars/all.yml`: `vpac_nodes`, `ceph.*` (especially `bootstrap_node`, `osd_devices`, `pools`, `cephfs_mountpoint`).

## Dependencies

- `ceph_bootstrap` (same stage, first half) — sets `ceph_fsid` and the mon is serving.
- `host_baseline` / `networking` / `virtualization` on every cluster node.

## Tags

- `ceph` — everything Ceph (also applies in `ceph_bootstrap`)
- `ceph-expand` — this role specifically
- `ceph-facts`, `ceph-hosts`, `ceph-osds`, `ceph-fs`, `ceph-mount`, `ceph-verify` — sub-steps

## Simplifications in v1

- Only the first `type: cephfs` entry in `ceph.pools[]` gets created. Multiple CephFS filesystems = future work.
- RBD pools aren't provisioned here (VM disks can live on CephFS; RBD is an optimization for specific profiles like ssc600).
- Admin keyring is pushed to every node. Production wants a scoped `client.libvirt` keyring with read-only access to ceph metadata; that's a future refinement.
- No pool-level placement rules, PG autoscaling is left at cephadm defaults.
- MDS is deployed by `ceph fs volume create` on the bootstrap node only. For HA MDS, future work layers a second standby MDS on another node.
