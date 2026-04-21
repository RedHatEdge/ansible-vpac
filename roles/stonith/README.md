# stonith

**Status: stub — not yet implemented.**

Stage 75 of the vPAC site deployment.

Runs **before** VM deploy (stage 80) — no production VM lands on a cluster without working fencing. See LEARNED-FIXES: field deployment April 15 ran without STONITH, hit a 20-second bridge churn, and a VM ran concurrently on two nodes against shared storage.

## Planned behavior

- For each entry in `vpac_nodes`, create a fence device named `fence-<hostname>` using the configured `stonith.fence_agent` (`fence_ipmilan` on real hardware, `fence_virsh` in the lab)
- `fence_ipmilan` path uses `bmc_ip`, `bmc_user`, `bmc_password` per node with `lanplus=true`, `cipher=3`
- `fence_virsh` path uses `stonith.virsh_host`, `stonith.virsh_user`, `stonith.virsh_identity_file` — node SSHes back to the libvirt host
- Location constraints: each fence resource has `pcs constraint location <fence-X> avoids <node-X>` so a node never tries to fence itself
- `pcs property set stonith-enabled=true` as the final task
- Dry-run: `stonith_admin --list-registered` confirms all devices registered; `pcs stonith status` shows all devices `Started`
- Optional: `fence_<agent> ... -o status` against each BMC to prove the agent can see the target (guarded by `stonith.dry_run_actual_probe`)

## Dependencies

- `pacemaker_base` (stage 70) — cluster must be up
- Preflight must have verified BMC/virsh reachability earlier in the run

Gated on `len(vpac_nodes) >= 3`.

## Tags

- `stonith` — full role (currently a no-op stub)
