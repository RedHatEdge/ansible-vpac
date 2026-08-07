# networking

Stage 20. Declarative per-host network configuration via `nmstate`.

Takes `networks`, `networking_defaults`, `bridges`, `vpac_nodes`, and per-host `host_vars` overrides, renders a single nmstate YAML document, and applies it with rollback-on-failure.

## What it configures, per host

From the inventory shape in `group_vars/all.yml`:

| Network | NIC layout | Result |
|---|---|---|
| `mgmt` | bond → bridge | bond on declared members → `br-mgmt` with `mgmt_ip`; default route out |
| `storage` | bond → raw IP | bond on declared members → `storage_ip` directly on the bond |
| `station` | bond → bridge | bond on declared members → `station-nic` (matches production) with `station_ip`; relay VMs attach here |
| `heartbeat` | raw NIC → raw IP (default), **or** VLAN-on-bond (opt-in) | dedicated: `heartbeat_ip` on a dedicated NIC (not a bridge, not a bond slave). shared: `heartbeat_ip` on a `bondN.<vlan>` subinterface — see **Heartbeat modes** below |
| `ptp` | raw NIC, **no IP** | NIC up but untouched — neither bridged nor bonded, no IP config |

VLAN fields (`networks.<name>.vlan`) are honored — when set, a VLAN subinterface is inserted between the bond and the bridge (mgmt/station), between the bond and the raw IP (storage), or as the heartbeat interface itself (shared heartbeat mode).

## Heartbeat modes

The corosync/pacemaker ring network is carried one of two ways, selected in inventory:

- **Dedicated NIC (default).** `networking_defaults.heartbeat_nic` names a raw port; `heartbeat_ip` lands on it directly. Production default, unchanged.
- **Shared VLAN-on-bond (opt-in).** Set `networks.heartbeat.shared_bond` to a bond role (`storage` | `mgmt` | `station`, or a bond ifname) and `networks.heartbeat.vlan` to a VLAN ID. The role renders a `bondN.<vlan>` subinterface carrying `heartbeat_ip` and no dedicated NIC is needed — this fits hardware with only four NICs (e.g. Dell XR4510c) that otherwise can't seat all five logical networks.

Why this is sound: **Red Hat support does not require a dedicated interconnect** — KB 3068841 states a cluster may communicate over an interface shared for another purpose, and bonding is supported. The real constraint is that each corosync ring live on a **different network**; a VLAN subinterface with its own subnet satisfies it (knet supports up to 8 rings). The **storage bond is the recommended carrier** — it is L2-only and not subject to the guest-bridge churn the mgmt bridge suffers, which is exactly why `preflight` already blesses a heartbeat/storage co-location. Latency guidance (KB 2823721): ≤2 ms RTT optimal, instability above ~300 ms.

**The heartbeat VLAN must be a distinct VLAN ID and subnet from storage.** Sharing the storage *bond* is the point; sharing the storage *VLAN/subnet* is not — it collapses ring separation into one broadcast domain and trips preflight's subnet-uniqueness check. E.g. if storage is on VLAN 30, put heartbeat on its own VLAN (23, etc.) with its own subnet. The switch ports for that bond must trunk both VLAN IDs.

The five derived vars that drive this (`networking_heartbeat_shared`, `_bond_name`, `_iface`, `_shared_bond_role`, `networking_skip_heartbeat`) live in the inventory (`group_vars/all.yml`), **not** in this role's `defaults/` — they are consumed by `preflight` as well, and role defaults are role-scoped.

`heartbeat_ip` (per node) and the heartbeat CIDR are identical in both modes — only the interface that carries the IP changes. `pacemaker_base` (stage 70) binds each node's ring to its `heartbeat_ip` regardless of which interface holds it.

## Bond options

Each `networking_defaults.<bond>` entry carries an `options` map that is rendered into nmstate's `link-aggregation.options`. Defaults shipped in `inventory/example/group_vars/all.yml`:

- **active-backup bonds** (`mgmt_bond`, `station_bond`) — `miimon: 100` (carrier polling every 100 ms; far faster than the ARP-probe default), `primary: <first member>` (preferred member when both are healthy).
- **802.3ad bonds** (`storage_bond`) — `miimon: 100`, `xmit_hash_policy: layer3+4` (spread flows by IP+port, best for many small Ceph connections), `lacp_rate: fast` (~3 s failover vs the ~30 s slow default).

Override per-site by editing `networking_defaults.<bond>.options` in inventory.

## Firewalld zones

After apply, interfaces are assigned to firewalld zones:

| Interface | Zone |
|---|---|
| `br-mgmt` | `{{ firewalld_default_zone }}` (usually `public`) |
| Storage bond (or VLAN subif) | `trusted` |
| `br-station` | `internal` |
| Heartbeat interface (NIC or `bondN.<vlan>`) | `trusted` |
| PTP NIC | `trusted` (PTP traffic must not be filtered) |

## Safety

Each apply uses `nmstatectl apply --timeout 60` — if the SSH session drops during apply (e.g. because we misconfigured the mgmt interface), NetworkManager auto-rolls back. Operators still lose one iteration, but the cluster stays reachable.

After apply, `verify.yml` additionally:

- Asserts every declared IP landed on its expected interface (`mgmt_ip`, `storage_ip`, `station_ip`, and `heartbeat_ip` on whichever interface carries it — dedicated NIC or shared VLAN).
- Asserts the PTP NIC is up but has no IPv4 address.
- Lists `ip route show | grep linkdown` and fails if any next-hop interface has no carrier — a linkdown bridge can mask duplicate-subnet collisions and let bad configs pass validation.
- Re-enumerates host IPv4 networks (resolved via `ipaddress.ip_network`) and asserts each subnet is unique to one interface — defense-in-depth against an apply that introduces a new collision after preflight cleared.

## Variables

| Name | Default | Notes |
|---|---|---|
| `nmstate_apply_timeout` | `60` | seconds; nmstate rolls back if apply doesn't confirm in time |
| `networking_disable_stp` | `true` | STP off on the VM-facing bridges; STP churn under guest-bridge load is documented to starve corosync heartbeats |

The derived heartbeat vars (`networking_heartbeat_shared`, `_bond_name`, `_iface`, `_shared_bond_role`, `networking_skip_heartbeat`) are defined in the inventory `group_vars/all.yml` (**not** here) because `preflight` consumes them too and role defaults are role-scoped.

Reads the full `networks`, `networking_defaults`, `bridges`, and `vpac_nodes` trees.

## Tags

- `networking` — everything
- `networking-packages` — nmstate + NetworkManager install
- `networking-apply` — render + nmstatectl apply
- `networking-firewall` — zone assignment
- `networking-verify` — post-apply sanity

## Dependencies

- `host_baseline` must have run (firewalld running, NM installed, `/etc/hosts` populated).
- Post-apply verification of PTP NIC isolation is handled by the separate `ptp_isolation` role. `playbooks/20-networking.yml` imports both this role AND `ptp_isolation` in sequence — `ptp_isolation` is intentionally a separate role so it stays tag-addressable (`--tags ptp-isolation`) and so it can be re-invoked from `ptp_timesync` (stage 40) for a third defense-in-depth check before `ptp4l` is armed.

## Limited-NIC hardware

Five logical networks do not require five physical ports. Two levers fit them onto fewer NICs:

- **mgmt / storage / station** already collapse onto shared bonds via VLANs (`networks.<name>.vlan`).
- **heartbeat** rides a VLAN on an existing bond in **shared mode** (see *Heartbeat modes*) — e.g. on a **4-NIC** node (Dell XR4510c), put the corosync ring on a VLAN on the storage bond and no fifth port is needed.
- **PTP** stays on its own port (`ptp_isolation` requires an unenslaved NIC) — this is the one role that genuinely wants a dedicated interface.

For a 3-NIC lab VM with no heartbeat at all, leave both `networking_defaults.heartbeat_nic` empty and `networks.heartbeat.shared_bond` unset; the heartbeat interface is skipped. `preflight` warns. This is fine for bringing up stages 10–60; a heartbeat interface (dedicated NIC or shared VLAN) must exist before **stage 70** (`pacemaker_base`), which binds each node's corosync ring to its `heartbeat_ip`. (There is no code that "refuses to proceed" without a *dedicated* NIC — pacemaker only needs the `heartbeat_ip` to be present on some interface.)
