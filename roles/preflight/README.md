# preflight

Inventory + host validation. Runs before any stage that mutates a target node. Fails fast on any issue that would bite later — misconfigured networks, isolated-CPU math that doesn't fit, PTP NIC attached to a bridge, unreachable BMC, missing or wrong-mode package sources.

## What it checks

| Check | Tag | Scope | Notes |
|---|---|---|---|
| SSH + sudo | `preflight-connectivity` | per host | Ping + passwordless sudo |
| RHEL version | `preflight-connectivity` | per host | Must be 9.x |
| Disk space | `preflight-connectivity` | per host | `/` and `/var` thresholds |
| Clock skew | `preflight-connectivity` | cluster | Nodes within `preflight_max_clock_skew_s` of the control host |
| Inventory shape | `preflight-inventory` | once | `vpac_nodes` non-empty; hostnames match inventory |
| Network CIDRs | `preflight-network` | once | Heartbeat vs mgmt overlap (gated on `len(vpac_nodes) >= 3`) |
| Declared NICs | `preflight-network` | per host | Each NIC in `networking_defaults` exists on the host |
| Subnet uniqueness | `preflight-subnet-uniqueness` | per host | No two interfaces share an IPv4 network (defends against the multi-bridge reboot-loop failure mode where the kernel oscillates source-NIC selection) |
| Hostname resolution | `preflight-hosts` | per host | `getent hosts <node>` and `getent hosts <node>-storage` return the expected IPs; each name on its own line in `/etc/hosts` |
| PTP NIC isolation | `preflight-ptp` | per host | Not a bridge member, bond slave, or macvtap target |
| PTP NIC capability | `preflight-ptp` | per host | `ethtool -T` reports hardware timestamping support |
| Hugepage math | `preflight-hugepages` | per host | `nr_hugepages × size < node_ram × 0.9` |
| STONITH reach | `preflight-stonith` | per host | `fence_ipmilan`: BMC answers; `fence_virsh`: SSH identity works |
| Mode: connected | `preflight-mode` | once | RHSM/Satellite URL reachable + creds auth |
| Mode: airgapped | `preflight-mode` | once | Local mirror + registry reachable; installer-ISO fingerprint present on nodes |

## Variables

Defaults in `defaults/main.yml`. Tune with care:

- `preflight_warn_only: false` — set `true` to turn all failures into warnings (for initial lab bring-up; never in production)
- `preflight_required_rhel_major: 9`
- `preflight_required_rhel_minor_min: 5` — reject anything older
- `preflight_min_root_gb: 20` — minimum free space on `/`
- `preflight_min_var_gb: 40` — minimum free space on `/var` (libvirt images, Ceph)
- `preflight_max_clock_skew_s: 30` — reject cluster if nodes drift more than this
- `preflight_check_*: true` — per-check on/off toggles. Set to `false` to skip a specific check.

## Dependencies

Reads from `group_vars/all.yml`: `deployment_mode`, `sources.*`, `vpac_nodes`, `networks.*`, `networking_defaults.*`, `rt_tuning.*`, `stonith.fence_agent`.

No handlers. No writes to target hosts (read-only).

## Tags

All checks run under the umbrella `preflight` tag. Sub-tags above let you run one group in isolation:

```bash
ansible-playbook -i inventory/mysite site.yml --tags preflight-network
```
