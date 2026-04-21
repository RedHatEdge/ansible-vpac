# ceph_expand

**Status: stub — not yet implemented.**

Stage 60 (second half) of the vPAC site deployment.

## Planned behavior

- Runs against `ceph_nodes` (bootstrap node plus the rest)
- Add each node to the Ceph cluster via `cephadm shell -- ceph orch host add <hostname> <storage_ip>`
- For each node, add OSDs from `ceph.osd_devices[hostname]` — no "all unused devices" auto-discovery (too risky)
- Create CephFS pools per `ceph.pools[]`: data pool + metadata pool with the declared PG counts
- Enable the MDS daemon, create the filesystem
- Mount CephFS at `ceph.cephfs_mountpoint` on every node in `vpac_cluster` (VM disks live here)
- Health-check: `ceph -s` must return `HEALTH_OK` before the role declares success

## Dependencies

- `ceph_bootstrap` (same stage, first half) — FSID and admin keyring must be in cluster facts
- All of stages 10–30 on every node

## Tags

- `ceph` — full role (currently a no-op stub)
