# Architecture

## What a vPAC cluster is

A Virtual Protection Architecture Cluster (vPAC) is a 3-node RHEL 9 cluster that hosts utility protection and automation workloads — IEC 61850 relays (SSC600-style), RTAC/RTU/VPR applications, and Windows engineering workstations — as virtual machines with real-time tuning and shared storage.

It replaces a rack of single-purpose hardware relay panels with a single HA platform that can host multiple vendors' protection software side-by-side, migrate workloads between nodes for maintenance, and recover from a node failure in seconds.

## Components

| Layer | Technology | Role |
|---|---|---|
| Operating system | RHEL 9 | Base platform, real-time tuning |
| Hypervisor | KVM + libvirt | VM lifecycle, CPU pinning, hugepages |
| Shared storage | Ceph (cephadm) + CephFS | VM disk images, VM portability |
| Cluster manager | Pacemaker + Corosync | VM placement, failover, quorum |
| Fencing | STONITH (fence_ipmilan) | Split-brain prevention |
| Time sync | PTP (IEEE 1588) + chrony | Sub-microsecond sync for relays |
| Real-time tuning | tuned, isolcpus, hugepages, RT chrony | Deterministic VM latency |

## Network layout

Five logical networks. Collapsing them onto fewer physical NICs is possible but some combinations are **hazardous** and the playbooks reject them:

| Network | Purpose | Typical separation |
|---|---|---|
| Management | Ansible, SSH, libvirt mgmt | Bridge `br-mgmt`, reachable from the SA's workstation |
| Storage | Ceph public + cluster | Dedicated bond, no bridge, no gateway (L2-only) |
| Station bus | IEC 61850 GOOSE/SV | Bridge `br-station`, VLAN-trunked to relays / process bus |
| Heartbeat | Corosync ring | **Dedicated NIC or VLAN**, not a bridge member — see below |
| PTP | Time sync | **Dedicated NIC**, not a bridge member — see below |
| BMC | STONITH | Usually a separate physical OOB network |

### Why heartbeat must not share the VM management bridge

On a shared `br-mgmt`, any bridge churn from guest VMs (frequent restarts, mass `vnet*` creation) causes STP flapping and packet drops. Corosync heartbeats ride on the same bridge and start timing out. Once heartbeats are lost, Pacemaker splits the cluster. In one deployment this caused two corosync partition events 20 seconds apart and a permanent pacemaker shutdown on one node, with a VM running simultaneously on two nodes against the same CephFS image.

Mitigation: corosync runs on a **dedicated network** (physical NIC or VLAN with its own bridge) that does not carry VM libvirt traffic.

### Why PTP must not share a NIC with macvtap or bridges

macvtap passthru on a NIC captures ethernet frames at the driver level. PTP event messages destined for the host can be consumed by guest VMs instead of by `ptp4l`, producing `SYNCHRONIZATION_FAULT` every few seconds.

Mitigation: PTP runs on a **dedicated NIC** that is not a bridge member, not a bond slave, not a macvtap target. The `ptp_isolation` role verifies this.

## Node roles

In the reference design all three nodes are identical peers at the cluster level (each runs MON+MGR+OSD for Ceph and is a full Pacemaker member). Workload placement differs:

- **Nodes A and B** host real-time relay VMs (SSC600-style, VPR-style). Identical hardware is recommended so VMs can migrate between them.
- **Node C** hosts the Windows engineering workstation with PCI NIC passthrough (for Wireshark on process-bus traffic). NICs used for passthrough are not attached to host bridges.

This split is configurable via the `rt_hosts` and `windows_hosts` inventory groups.

## Real-time guarantees

- Dedicated CPUs isolated from the kernel scheduler (`isolcpus=` + `nohz_full=` + `rcu_nocbs=`)
- Per-VM CPU pinning (vcpupin + emulatorpin + iothreadpin)
- 1 GB hugepages for relay VMs, locked memory, `memballoon` disabled
- FIFO priority per VM (higher for more latency-sensitive VMs)
- RT chrony settings (`lock_all`, elevated `sched_priority`, `combinelimit 0`)
- cpufreq governor `performance`
- `sched_rt_runtime_us=-1`

Target: cyclictest tail latency under 120 µs on RT hosts. Validated by the `validate` role.

## High availability

- **Quorum**: 3 nodes, simple majority. Two-node quorum-device setups are possible but not default — document at `OPERATIONS.md`.
- **Fencing**: `fence_ipmilan` per node, one fence resource each, location constraint so a node can't fence itself. `stonith-enabled=true` is set before any production VM lands.
- **VM HA**: each VM is a Pacemaker resource with `meta allow-migrate=true` where live migration is desired, or hard location constraints to pin VMs to specific hosts.
- **Planned reboot**: `pcs node standby` + `systemctl reboot`. Never `pcs cluster stop` before a reboot (leaves a shutdown attribute in the CIB that blocks rejoin).

## Storage

Ceph via `cephadm` containers. One MON+MGR per node, one MDS for CephFS, OSDs per-node from explicit device lists (no auto-discovery). CephFS is mounted at `/vms/` on all nodes; VM disk images live there, so any node can take over any VM after a fence event.

Per-node OSD device lists are declared in `group_vars/all.yml` under `ceph.osd_devices` — the playbook refuses to provision OSDs on devices that aren't empty.

## Deployment model

A single Ansible run from a control workstation:

1. Operator fills in `inventory/<site>/group_vars/all.yml` and `hosts.yml`
2. Runs `ansible-playbook -i inventory/<site> site.yml`
3. Playbook runs stages in dependency order (see root `README.md`)
4. Validation stage produces a report; operator confirms targets are met

Re-running any stage via `--tags` is idempotent and safe.
