# stonith

Stage 75. Configures STONITH fence devices for every cluster node, sets the per-fence "avoids self" constraints, and flips `stonith-enabled` from `false` (the handoff state `pacemaker_base` leaves) to `true`.

Runs **before** `vm_deploy` (stage 80) — no production VM lands on a cluster without working fencing. Without STONITH, a 20-second bridge churn or a corosync glitch can leave the same VM running on two nodes simultaneously against shared storage (the documented split-brain corruption window).

Single-node deployments (`len(vpac_nodes) < 3`) skip entirely. Operator-disabled deployments (`stonith.enabled: false`) also skip; the role never half-configures.

## What it does

1. **Install fence agent** — installs `fence-agents-ipmilan` or `fence-agents-virsh` (whichever the inventory selected). RHEL 9 ships fence-agents split per-protocol; installing only the chosen agent keeps the cluster footprint minimal.
2. **Preflight** — asserts the agent is supported (`fence_ipmilan` or `fence_virsh`), the binary is now on PATH, `corosync.conf` is present (pacemaker_base ran), and (lab path) the `stonith.virsh_*` inventory vars are populated.
3. **Create fence devices** — branches on `stonith.fence_agent`:
   - **`fence_ipmilan`** (production): one device per node, `fence-<hostname>`, with `ipaddr=<bmc_ip>` `login=<bmc_user>` `passwd=<bmc_password>` `lanplus=<int>` `pcmk_host_list=<hostname>`. Idempotent (re-runs accept "already exists").
   - **`fence_virsh`** (lab): copies the operator-supplied SSH identity to every cluster node as **`0600 root:root`** (SSH refuses any private key with mode > 0600 — "permissions are too open"; fence_virsh runs as root via pacemaker, not as the hacluster user, so `haclient` group access isn't needed). Then creates one device per node with `pcmk_host_check=static-list`, `pcmk_host_list=<hostname>`, `pcmk_host_map=<hostname>:<libvirt_domain>` (the libvirt domain name often differs from the cluster hostname; default to hostname when `vpac_nodes[*].libvirt_domain` is unset).
4. **Constraints** — for every fence device, `pcs constraint location fence-<hostname> avoids <hostname>=INFINITY`. Idempotent: gates on the constraint listing so re-runs don't append duplicates with auto-generated IDs.
5. **Probe** *(optional)* — when `stonith_dry_run_actual_probe: true`, runs `fence_<agent> -o status` against each device. Proves the agent can reach the BMC (or libvirt host) without rebooting anything. Default off because some BMCs rate-limit query connections; turn on for first-deploy validation.
6. **Atomic enable** — reads `pcs stonith status` (cluster-wide CIB view, NOT `stonith_admin --list-registered` which is per-node-aware and omits a node's own fence device because of the avoids-self constraint), asserts every expected `fence-<hostname>` is present, then `pcs property set stonith-enabled=true`. The atomicity matters: `stonith-enabled=true` with missing fence devices blocks all VirtualDomain resources cluster-wide because Pacemaker refuses to start a resource that requires fencing if it can't verify the fence path.
7. **Verify** — **polls** `pcs stonith status` for up to 60 seconds for every device to leave `Stopped` (pacemaker takes a few seconds to probe and start each device after the property flip — the assertion was previously racing this), confirms `stonith-enabled=true` via `pcs property show` (matching the EL9 / pcs 0.11+ `<key>=<value>` output format, NOT the older `<key>: <value>` form), and asserts no pending fencing actions in `pcs status`.

## Variables

| Name | Default | Notes |
|---|---|---|
| `stonith_dry_run_actual_probe` | `false` | run `fence_<agent> -o status` against each device |

Reads from `group_vars/all.yml`:

- `stonith.enabled` (default true) — operator off-switch
- `stonith.fence_agent` — `fence_ipmilan` or `fence_virsh`
- `stonith.default_lanplus` — for fence_ipmilan, default true
- `stonith.virsh_host`, `stonith.virsh_user`, `stonith.virsh_identity_file` — for the lab path; the file at `virsh_identity_file_local` (controller side) is copied to `virsh_identity_file` (node side)
- `vpac_nodes[*].hostname`, `bmc_ip`, `bmc_user`, `bmc_password` — fence_ipmilan inputs per node
- `vpac_nodes[*].libvirt_domain` *(optional, lab only)* — when the libvirt domain name differs from the cluster hostname

## Tags

- `stonith` — everything
- `stonith-preflight`, `stonith-create`, `stonith-constraints`, `stonith-probe`, `stonith-enable`, `stonith-verify` — sub-steps

## Companion operational playbook

`playbooks/op-stonith-fence-test.yml` — interactive functional test that **WILL POWER-CYCLE** the target node. Required for first-deploy validation against real hardware:

```bash
ansible-playbook -i inventory/<site> playbooks/op-stonith-fence-test.yml \
  -e fence_target=<node-hostname> \
  -e i_have_drained_vms=yes
```

Both vars are required and the second must equal `yes` verbatim (the playbook refuses to run otherwise). Pick the safest available target — typically the node without RT or HMI VMs.

## Coordination with `pacemaker_base`

`pacemaker_base` (stage 70) leaves `stonith-enabled=false` as a deliberate handoff state. This role flips it to `true`. The pending-fence recovery primitives (`/usr/local/sbin/pcs-stonith-confirm-helper` script + `playbooks/op-pacemaker-recover.yml` cluster cold-start playbook) are shipped by `pacemaker_base` and not duplicated here — `verify.yml` points operators at them when it detects pending fencing.

## Intentional v1 omissions

- `priority-fencing-delay`, `concurrent-fencing`, `pcmk_delay_max`, `pcmk_reboot_action`, `pcmk_monitor_timeout` — none are set by the documented field deployment. v1 matches that shape; addable as `stonith.advanced.*` later if a site needs anti-race tuning.
- Automated `pcs stonith fence` end-to-end test — the role registers and verifies devices but does NOT power-cycle nodes. Use `op-stonith-fence-test.yml` for the documented interactive procedure.

## Dependencies

- `host_baseline` (stage 10) — `firewalld` open for `high-availability`
- `networking` (stage 20) — heartbeat NIC up
- `preflight` — BMC reachability (fence_ipmilan) or virsh SSH reachability (fence_virsh)
- `pacemaker_base` (stage 70) — cluster up, `stonith-enabled=false` set
