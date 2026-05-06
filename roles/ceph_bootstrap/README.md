# ceph_bootstrap

Stage 60 (first half). Runs on the `ceph_bootstrap_node` group (a single-host group naming whichever node starts the cluster).

## What it does

1. **Preflight** — confirms `chronyc tracking` shows `Leap status Normal` (Ceph refuses quorum with drifted clocks — see LEARNED-FIXES). Confirms this node's `storage_ip` is bound to an interface (the networking role's job).
2. **Packages** — installs `cephadm`, `podman`, `ceph-common` from the `rhceph-<release>-tools-for-rhel-9-x86_64-rpms` entitled repo. Writes a container registry trust drop-in when `sources.container_registry_insecure: true` (local builder registry over HTTP).
3. **Bootstrap** — runs `cephadm --image <rhcs-image> bootstrap --mon-ip <storage_ip> --cluster-network <storage_cidr> --ssh-user <ceph.ssh_user|root> --allow-fqdn-hostname --skip-monitoring-stack`. `--ssh-user` lets cephadm manage other nodes as a non-root admin when root SSH is locked down. `--skip-monitoring-stack` defers prometheus/alertmanager/grafana/node-exporter placement until `ceph_expand` can apply them with the right image paths (they live at different registry paths than the core RHCS image). Adds `--registry-json` when `ceph.registry_credentials_file` is set (required for registry.redhat.io auth). Skips the dashboard by default. Idempotent — checks for `/etc/ceph/ceph.conf` first and skips if the cluster is already bootstrapped.
4. **Network config** — sets `ceph config set global public_network <ceph.public_network>` and `cluster_network <ceph.cluster_network>` authoritatively (cephadm's bootstrap flags are unsupported / hint-only in current versions). Also binds the dashboard to `ceph_bootstrap_dashboard_server_addr` (default `0.0.0.0`) so the operator's mgmt-VLAN laptop can reach it — default binds only to the storage IP. **Forces a respawn of the bootstrap MGR via `ceph mgr fail`** (rather than `ceph orch restart mgr`) so the new dashboard config takes effect on a fresh cluster — `orch restart` requires a standby MGR, which doesn't exist immediately after `cephadm bootstrap`. After `ceph_expand` adds standby MGRs both options work; `mgr fail` keeps the bootstrap path single-step and idempotent.
5. **Monitoring config** — writes `mgr/cephadm/container_image_{prometheus,alertmanager,node_exporter,grafana}` pointing at the paths derived from `sources.container_registry` + `container_images.{prometheus,...}`. Runs right after network config so that when `ceph_expand` triggers monitoring deployment, cephadm pulls from the correct registry.
6. **Verify** — captures the FSID as an Ansible fact (cacheable, used by `ceph_expand`), then waits for the MON to report at least `HEALTH_WARN`. `HEALTH_OK` comes later once OSDs are up.

## Variables

| Name | Default | Notes |
|---|---|---|
| `ceph_bootstrap_enable_dashboard` | `true` | flip `false` if you want only Grafana (no native Ceph dashboard). When on, reach it at `https://<active-mgr>:8443`; cephadm auto-generates an admin password printed to the bootstrap log — reset with `echo '<pw>' \| ceph dashboard ac-user-create admin -i - administrator` |
| `ceph_bootstrap_dashboard_server_addr` | `"0.0.0.0"` | dashboard bind address; restrict to a specific IP if you want it reachable only from one network |
| `ceph_bootstrap_mon_health_timeout_s` | `120` | max wait for MON health |
| `ceph_bootstrap_cmd_timeout_s` | `600` | max wait for `cephadm bootstrap` itself |

Reads from `group_vars/all.yml`: `vpac_nodes`, `ceph.*` (including `registry_credentials_file`), `sources.container_registry`, `sources.container_registry_insecure`, `container_images.*`.

## Dependencies

- `host_baseline` (stage 10) — `/etc/hosts` + chrony sync + `rhceph-<release>-tools-for-rhel-9-x86_64-rpms` enabled
- `networking` (stage 20) — storage network UP with the declared IP
- `virtualization` (stage 30) — podman is installed here (also as a dep of cephadm)

For airgapped deployments, `01-build-builder.yml` (run once against the builder host) must have populated the local RPM mirror and container registry with the RHCS tools repo and container image before this role runs.

## Tags

- `ceph` — everything Ceph (also applies in `ceph_expand`)
- `ceph-bootstrap` — this role specifically
- `ceph-preflight`, `ceph-packages`, `ceph-network-config`, `ceph-monitoring`, `ceph-verify` — sub-steps

## Facts set

- `ceph_fsid` — FSID of the bootstrapped cluster. Cacheable, consumed by `ceph_expand`.
