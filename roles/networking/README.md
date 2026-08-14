# networking

Stage 20. Declarative per-host network configuration via `nmstate`.

Takes `networks`, `networking_defaults`, `bridges`, `vpac_nodes`, and per-host `host_vars` overrides, renders a single nmstate YAML document, and applies it with rollback-on-failure.

## What it configures, per host

From the inventory shape in `group_vars/all/main.yml`:

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

The five derived vars that drive this (`networking_heartbeat_shared`, `_bond_name`, `_iface`, `_shared_bond_role`, `networking_skip_heartbeat`) live in the inventory (`group_vars/all/main.yml`), **not** in this role's `defaults/` — they are consumed by `preflight` as well, and role defaults are role-scoped.

`heartbeat_ip` (per node) and the heartbeat CIDR are identical in both modes — only the interface that carries the IP changes. `pacemaker_base` (stage 70) binds each node's ring to its `heartbeat_ip` regardless of which interface holds it.

## Mapping physical NICs to the inventory (operator workflow)

You have a chassis full of ports and five logical networks to land on them. This
is how you go from the hardware to the inventory files the role reads.

### 1. Identify the ports on the hardware

On each node, list the interfaces and match them to physical ports by cabling:

```bash
ip -br link                     # interface names + MAC + up/down
ethtool <nic>                   # link speed / carrier
ethtool -i <nic>                # driver + bus-info (PCI address) — pin a name to a slot
ethtool -T <nic>                # PTP HW timestamping — REQUIRED on the PTP-role port
```

Record, per node, which interface name (`eno8303`, `ens1f0`, …) carries which
role. RHEL's predictable names are stable **on a given machine** but differ across
hardware models — so identical nodes share one `networking_defaults`, and any node
that differs gets a `host_vars/<node>.yml` override (step 5).

### 2. Physical layout → `networking_defaults`

This block names the *physical* interfaces and how they aggregate:

```yaml
networking_defaults:
  mgmt_bond:    { name: bond0, mode: active-backup, members: [eno1, eno2], options: {miimon: 100, primary: eno1} }
  storage_bond: { name: bond1, mode: 802.3ad,       members: [ens1f0, ens1f1], options: {miimon: 100, xmit_hash_policy: layer3+4, lacp_rate: fast} }
  station_bond: { name: bond2, mode: active-backup, members: [ens2f0, ens2f1], options: {miimon: 100, primary: ens2f0} }
  ptp_nic: ens4f2        # the dedicated PTP port (never bonded/bridged)
  heartbeat_nic: ens3f0  # dedicated heartbeat NIC — OR leave "" and use shared mode (below)
```

### 3. Logical networks → `networks`

CIDR, gateway, and VLAN per role (`mgmt`, `storage`, `station`, `heartbeat`, `bmc`).
For the shared-heartbeat mode, set `heartbeat.shared_bond` + a distinct `heartbeat.vlan`
(see *Heartbeat modes*).

### 4. Per-node addresses → `vpac_nodes`

One entry per node with its `mgmt_ip` / `storage_ip` / `station_ip` / `heartbeat_ip` /
`bmc_ip` (and BMC creds). The interface *names* live in `networking_defaults`; only the
addresses are per node here.

### 5. Per-node NIC overrides → `host_vars/<node>.yml`

When a node's ports are named differently (mixed hardware), override just the
changed keys of `networking_defaults` in that node's `host_vars`. Everything else
inherits the group default.

### Worked example — a 4-NIC node (heartbeat shares the storage bond)

```yaml
# group_vars/all/main.yml
networking_defaults:
  mgmt_bond:    { name: bond0, mode: active-backup, members: [eno8303] }   # 1-member "bond" = single NIC
  storage_bond: { name: bond1, mode: active-backup, members: [eno8403] }
  station_bond: { name: bond2, mode: active-backup, members: [eno8503] }
  ptp_nic: eno8603
  heartbeat_nic: ""                     # no dedicated NIC — shared mode carries it
networks:
  storage:   { cidr: 10.10.30.0/24, vlan: 30 }
  heartbeat: { cidr: 10.10.23.0/24, vlan: 23, shared_bond: storage }  # ring on VLAN 23 over the storage bond
```
Four physical NICs (`eno8303/8403/8503/8603`) carry all five networks: mgmt, storage,
station, PTP, and heartbeat-as-a-VLAN-on-storage. The switch ports for the storage
bond must trunk **both** VLAN 30 and 23.

## Production vs lab / POC — what to relax, what never to

| Concern | Production | Lab / POC |
|---|---|---|
| Bonding (mgmt/storage/station) | Two-member bonds for link redundancy | One-member bonds are fine (no second port to spare) |
| Storage link | 10 GbE+ (`validate.storage_nic_min_mbps: 10000`) | Set `validate.storage_nic_min_mbps: 1000` — 1 GbE works, just slower Ceph recovery |
| Heartbeat | Dedicated NIC preferred; shared-VLAN-on-storage-bond is supported and field-proven for 4-NIC nodes | Shared-VLAN-on-storage-bond, or omit entirely for a pre-cluster single-node bring-up |
| PTP timestamping | HW-timestamping NIC required (`ethtool -T` shows a PHC) | `ptp_timesync_require_hw_timestamping: false` to run on a sw-only NIC |
| BMC network | Physically separate | Can share the management switch, still its own subnet |

**Lines that do NOT relax, even in a lab:**

- **The PTP NIC is never bridged, bonded, or a macvtap target** — the role refuses it (`ptp_isolation`). A shared PTP NIC silently loses the clock.
- **The corosync heartbeat rides its own VLAN and subnet** — sharing the storage *bond* is fine; sharing the storage *VLAN/subnet* collapses ring separation and trips the subnet-uniqueness check.
- **SELinux stays on** and the management path stays firewalled.

## Bond options

Each `networking_defaults.<bond>` entry carries an `options` map that is rendered into nmstate's `link-aggregation.options`. Defaults shipped in `inventory/example/group_vars/all/main.yml`:

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

The derived heartbeat vars (`networking_heartbeat_shared`, `_bond_name`, `_iface`, `_shared_bond_role`, `networking_skip_heartbeat`) are defined in the inventory `group_vars/all/main.yml` (**not** here) because `preflight` consumes them too and role defaults are role-scoped.

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
