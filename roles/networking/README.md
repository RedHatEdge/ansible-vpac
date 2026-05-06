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
| `heartbeat` | raw NIC → raw IP | `heartbeat_ip` on a dedicated NIC (not a bridge, not a bond slave) |
| `ptp` | raw NIC, **no IP** | NIC up but untouched — neither bridged nor bonded, no IP config |

VLAN fields (`networks.<name>.vlan`) are honored — when set, a VLAN subinterface is inserted between the bond and the bridge (or between the bond and the raw IP for storage).

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
| Heartbeat NIC | `trusted` |
| PTP NIC | `trusted` (PTP traffic must not be filtered) |

## Safety

Each apply uses `nmstatectl apply --timeout 60` — if the SSH session drops during apply (e.g. because we misconfigured the mgmt interface), NetworkManager auto-rolls back. Operators still lose one iteration, but the cluster stays reachable.

After apply, `verify.yml` additionally:

- Asserts every declared IP landed on its expected interface (`mgmt_ip`, `storage_ip`, `station_ip`, `heartbeat_ip`).
- Asserts the PTP NIC is up but has no IPv4 address.
- Lists `ip route show | grep linkdown` and fails if any next-hop interface has no carrier — a linkdown bridge can mask duplicate-subnet collisions and let bad configs pass validation.
- Re-enumerates host IPv4 networks (resolved via `ipaddress.ip_network`) and asserts each subnet is unique to one interface — defense-in-depth against an apply that introduces a new collision after preflight cleared.

## Variables

| Name | Default | Notes |
|---|---|---|
| `nmstate_apply_timeout` | `60` | seconds; nmstate rolls back if apply doesn't confirm in time |
| `networking_skip_heartbeat` | computed | auto-skips heartbeat if `networking_defaults.heartbeat_nic` is empty |
| `networking_disable_stp` | `true` | STP off on the VM-facing bridges; STP churn under guest-bridge load is documented to starve corosync heartbeats |

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

## Lab limitations

The lab VMs have 3 NICs. The heartbeat NIC requires a 4th NIC; until that is added, set `networking_defaults.heartbeat_nic: ""` in lab inventory to skip the heartbeat interface entirely. `preflight` warns on this; `pacemaker` will refuse to proceed.
