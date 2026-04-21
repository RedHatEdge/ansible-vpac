# vm_deploy

**Status: stub — not yet implemented.**

Stage 80 (second half) of the vPAC site deployment.

## Planned behavior

Depends on cluster shape:

**Cluster (len(vpac_nodes) >= 3):**
- `virsh define` each VM from the XMLs written by `vm_templates`
- Create each VM as a Pacemaker `VirtualDomain` resource with `pcs resource create <name> ocf:heartbeat:VirtualDomain config=<xml> migration_transport=ssh`
- Apply location constraints from `vm_catalog[].target_host` (preferred node) and `allowed_hosts` (eligible nodes)
- Set per-VM `resource-stickiness` appropriate for the workload
- Start the resources; confirm `pcs status` shows them `Started` on the expected node

**Single-node (len(vpac_nodes) == 1):**
- `virsh define` each VM, `virsh autostart`, `virsh start`
- No Pacemaker involvement

## Dependencies

- `vm_templates` (same stage) — XMLs must exist
- `pacemaker_base` + `stonith` (stages 70, 75) — for cluster mode
- `ceph_expand` (stage 60) — for shared storage

## Tags

- `vm` — full role (currently a no-op stub)
