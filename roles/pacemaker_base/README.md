# pacemaker_base

Stage 70. Brings up the Pacemaker/Corosync cluster on the dedicated heartbeat network and primes it for `stonith` (stage 75) and `vm_deploy`-Pacemaker-managed-mode to land on top.

Single-node deployments (`len(vpac_nodes) < 3`) skip this role entirely — VMs run as standalone libvirt domains.

## What it does

1. **Hard-fail preflight** — refuses to proceed if `networks[pacemaker_ring_network].cidr == networks.mgmt.cidr`. Corosync must run on a network separated from VM management traffic; collapsing them is the documented split-brain root cause.
2. **Packages** — installs `pcs`, `pacemaker`, `corosync`, `fence-agents-all`.
3. **Firewall** — asserts that `host_baseline` already opened `firewalld --add-service=high-availability` (covers corosync 5405/udp + pcsd 2224/tcp + the rest of the Pacemaker port set).
4. **hacluster user** — sets the password from `pacemaker.hacluster_password` (vault) using a stable salt derived from `pacemaker.cluster_name` so re-runs are idempotent.
5. **pcsd** — enables and starts the daemon on every node.
6. **`pcs host auth`** — runs once from the bootstrap node (`vpac_nodes[0]`) and propagates auth tokens to every node listed.
7. **`pcs cluster setup`** — gated on `/etc/corosync/corosync.conf` being absent on the bootstrap node so re-runs don't error with "Cluster is already configured." Each node's ring address is its `heartbeat_ip` from inventory (`pcs cluster setup <name> <hostname>=<heartbeat_ip>...`); without explicit per-node addresses, corosync would bind to whatever interface resolves `<hostname>` from `/etc/hosts`, which is typically the mgmt network.
8. **`pcs cluster start --all`** then **`pcs cluster enable --all`** — the `enable` is non-negotiable because `corosync` and `pacemaker` ship with `active/disabled` defaults; without `enable --all`, any node reboot orphans the cluster stack.
9. **Cluster properties + resource defaults** — sets `default-resource-stickiness`, `cluster-recheck-interval`, `no-quorum-policy=stop` (explicit), `stonith-enabled=false` (handed off to `stonith` role to flip true), plus `pcs resource defaults update` for `migration-threshold` + `failure-timeout`, and `pcs resource op defaults update` for the VirtualDomain operation timeout. Setting these once means `vm_deploy` doesn't have to repeat them per VM.
10. **Helper scripts** — drops three operator helpers under `/usr/local/sbin/` (see Operator helpers below), plus a `pcs-safe-reboot-finish.service` systemd oneshot.
11. **Verify** — `pcs status` reports no OFFLINE nodes and no pending fencing actions; per-node `corosync-cfgtool -s` confirms the ring is bound to that node's `heartbeat_ip` (defense-in-depth against `pcs cluster setup` accidentally falling back to `/etc/hosts`).

## Variables (with defaults)

| Name | Default | Notes |
|---|---|---|
| `pacemaker_ring_network` | `"heartbeat"` | network name from `networks.<name>` whose CIDR ring binds to |
| `pacemaker_default_resource_stickiness` | `100` | keep resources where they land |
| `pacemaker_cluster_recheck_interval` | `"2min"` | how often pacemaker re-evaluates time rules |
| `pacemaker_migration_threshold` | `3` | failed monitors before migration |
| `pacemaker_failure_timeout` | `"600s"` | how long failures persist |
| `pacemaker_no_quorum_policy` | `"stop"` | explicit (library default; set so it can't be flipped to `"ignore"`) |
| `pacemaker_virtualdomain_op_timeout` | `"120s"` | VirtualDomain operation timeout |
| `pacemaker_virtualdomain_monitor_interval` | `"30s"` | informational; set per-resource by vm_deploy |
| `pacemaker_virtualdomain_monitor_timeout` | `"30s"` | informational; set per-resource by vm_deploy |

Reads from `group_vars/all.yml`: `pacemaker.cluster_name`, `pacemaker.hacluster_password`, `vpac_nodes` (uses `hostname` and `heartbeat_ip`), `networks.heartbeat`, `networks.mgmt`.

## Operator helpers (installed under `/usr/local/sbin/`)

- **`pcs-safe-reboot`** — the right way to reboot a clustered node. Standby → wait for resources to vacate → enable the next-boot unstandby oneshot → `systemctl reboot`. Defends against the documented `pcs cluster stop`-before-reboot trap that leaves a CIB shutdown attribute and pacemaker exits with status 100 on rejoin.
- **`pcs-stonith-confirm-helper`** — interactive helper that lists pending fencing actions and offers to confirm them. Pending CIB fence actions persist across quorum loss and re-fire when quorum re-forms; clear them before bringing additional nodes online during recovery.
- **`pcs-cluster-precheck`** — pre-restart sanity gate. Verifies no lingering CIB shutdown attribute on this node, no pending fencing in the CIB, and no OFFLINE nodes. Exits non-zero on any finding. Run before any planned cluster restart.
- **`pcs-safe-reboot-finish.service`** — systemd oneshot enabled by `pcs-safe-reboot`. Runs `pcs node unstandby` on next boot, then disables itself.

## Companion operational playbook

`playbooks/op-pacemaker-recover.yml` automates the documented nine-step cluster cold-start procedure (start one designated node alone → disable stonith → confirm any pending fences → start remaining nodes one at a time → re-enable stonith last). Use after any incident that leaves the cluster fully down.

```bash
ansible-playbook -i inventory/<site> playbooks/op-pacemaker-recover.yml \
  -e recover_first_node=<node-hostname>
```

Pick the first node thoughtfully — it should be the most-likely-healthy node (no recent fence loop, no recent OOMs, most recent successful `pcs status` before the incident).

## Tags

- `pacemaker` — everything
- `pacemaker-preflight`, `pacemaker-packages`, `pacemaker-firewall`, `pacemaker-hacluster`, `pacemaker-pcsd`, `pacemaker-auth`, `pacemaker-setup`, `pacemaker-properties`, `pacemaker-helpers`, `pacemaker-verify` — sub-steps

## Dependencies

- `host_baseline` (stage 10) — needs `firewall-cmd --add-service=high-availability` already in effect via `firewalld_baseline_services`
- `networking` (stage 20) — needs the heartbeat NIC up with `heartbeat_ip` bound on every node
- `ceph_expand` (stage 60) — Ceph health is independent, but pacemaker before ceph would race the storage layer

`stonith` (stage 75) flips `stonith-enabled` from `false` to `true` after fence devices are configured.

## Re-run safety

The role is idempotent on re-runs except for one boundary: `pcs cluster setup` only runs when `/etc/corosync/corosync.conf` is absent on the bootstrap node. To rebuild a cluster intentionally, `pcs cluster destroy --all` first (interactively — the role refuses to automate destruction).
