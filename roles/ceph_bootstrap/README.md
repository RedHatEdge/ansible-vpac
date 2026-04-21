# ceph_bootstrap

**Status: stub — not yet implemented.**

Stage 60 (first half) of the vPAC site deployment.

## Planned behavior

- Run on `ceph.bootstrap_node` only
- Install `cephadm` from the mirror
- Pull the Ceph container image from `sources.container_registry` / `ceph.container_image` (default `ceph/ceph:v<release>`)
- `cephadm bootstrap --mon-ip <storage_ip> --image <container_image>` — specifies the storage-network IP so the MON binds there, not on the mgmt bridge
- Capture the generated FSID and admin keyring; write them into a cluster fact that `ceph_expand` can read on the other nodes
- Pre-req checked by `preflight`: storage network up, all cluster nodes resolvable by short-name via `/etc/hosts`

## Dependencies

- `host_baseline` (stage 10) — `/etc/hosts` must have all `<hostname>-storage` entries
- `networking` (stage 20) — storage-network interface must be up and reachable across nodes
- `virtualization` (stage 30) — `podman` or `docker` (depending on Ceph release) must be installed for cephadm containers

Gated on `len(vpac_nodes) >= 3`; single-node mode skips Ceph entirely.

## Tags

- `ceph` — full role (currently a no-op stub)
