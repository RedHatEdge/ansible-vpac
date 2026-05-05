# pacemaker_base

**Status: stub — not yet implemented.**

Stage 70 of the vPAC site deployment.

## Planned behavior

- Install `pcs`, `pacemaker`, `corosync`, `fence-agents-all`
- Enable + start `pcsd.service`
- Auth `hacluster` user across all cluster nodes using `pacemaker.hacluster_password` from the vault
- `pcs cluster setup <cluster_name> <node>=<heartbeat_ip> ...` — Corosync binds to the **dedicated heartbeat network** only, never to the VM-mgmt bridge (see LEARNED-FIXES: documented split-brain under bridge-traffic load)
- Start the cluster (`pcs cluster start --all`) and enable on boot (`pcs cluster enable --all`)
- Set `default_resource_stickiness` from inventory
- Gate hard-fail: if `networks.heartbeat.cidr == networks.mgmt.cidr` the role refuses to proceed (also enforced by preflight)

## Dependencies

- All of stages 10–60
- `networks.heartbeat` must be a distinct CIDR from `networks.mgmt`
- Vault must provide `vault_hacluster_password`

Gated on `len(vpac_nodes) >= 3` — single-node deployments skip Pacemaker and run VMs as standalone libvirt domains.

## Tags

- `pacemaker` — full role (currently a no-op stub)
