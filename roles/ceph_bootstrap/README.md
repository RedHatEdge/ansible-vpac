# ceph_bootstrap

Stage 60 (first half). Runs on the `ceph_bootstrap_node` group (a single-host group naming whichever node starts the cluster).

## What it does

1. **Preflight** — confirms `chronyc tracking` shows `Leap status Normal` (Ceph refuses quorum with drifted clocks — see LEARNED-FIXES). Confirms this node's `storage_ip` is bound to an interface (the networking role's job).
2. **Packages** — installs `cephadm` (which pulls `podman`).
3. **Bootstrap** — runs `cephadm --image <image> bootstrap --mon-ip <storage_ip> --cluster-network <storage_cidr>`. Skips the dashboard by default. Idempotent — checks for `/etc/ceph/ceph.conf` first and skips if the cluster is already bootstrapped.
4. **Verify** — captures the FSID as an Ansible fact (cacheable, used by `ceph_expand`), then waits for the MON to report at least `HEALTH_WARN`. `HEALTH_OK` comes later once OSDs are up.

## Variables

| Name | Default | Notes |
|---|---|---|
| `ceph_release_tag_map` | `{reef: v18, squid: v19, quincy: v17}` | maps `ceph.release` → image tag |
| `ceph_bootstrap_enable_dashboard` | `false` | flip `true` for the cephadm dashboard MGR module |
| `ceph_bootstrap_mon_health_timeout_s` | `120` | max wait for MON health |
| `ceph_bootstrap_cmd_timeout_s` | `600` | max wait for `cephadm bootstrap` itself |

Reads from `group_vars/all.yml`: `vpac_nodes`, `ceph.*`, `sources.container_registry`.

## Dependencies

- `host_baseline` (stage 10) — `/etc/hosts` + chrony sync
- `networking` (stage 20) — storage network UP with the declared IP
- `virtualization` (stage 30) — podman is a dep of cephadm; comes for free

## Tags

- `ceph` — everything Ceph (also applies in `ceph_expand`)
- `ceph-bootstrap` — this role specifically
- `ceph-preflight`, `ceph-packages`, `ceph-verify` — sub-steps

## Facts set

- `ceph_fsid` — FSID of the bootstrapped cluster. Cacheable, consumed by `ceph_expand`.
