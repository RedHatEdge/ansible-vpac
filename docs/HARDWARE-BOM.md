# Hardware reference / BOM

Reference hardware for a vPAC cluster. Specific models below are examples — adapt to what the customer already has or prefers. The playbooks don't hard-code any model; the variable contract handles differences between sites.

## Per-node compute

- 2 × Intel Xeon Scalable (Gold 5318N or equivalent, 24c / 48t) — supports sufficient pinned cores for multiple RT VMs while leaving housekeeping CPUs for the host
- 128-256 GB DDR4/5 ECC — enough for hugepages sized to the VM workload plus ~32 GB for the host and Ceph OSDs
- 3+ × NVMe SSDs for Ceph OSDs — sized per total VM footprint × 3 (replication) + growth
- 1 × boot SSD (separate from OSD devices)

## Network interfaces per node

Minimum five logical networks. Practical layout for identical hardware:

| NIC | Purpose | Notes |
|---|---|---|
| 2 × 1 GbE (on-board) | Management bond | LACP or active-backup |
| 2 × 10/25 GbE | Storage bond (Ceph) | LACP when ToR supports |
| 2 × 10/25 GbE | Station bus bond | Trunked for multiple VLANs |
| 1 × 1 GbE | Heartbeat | Standalone — no bond, no bridge |
| 1 × 1 GbE | PTP | Standalone — no bond, no bridge, no macvtap |
| 1 × OOB | BMC / IPMI | Physically separate network |

At sites where NICs are constrained, heartbeat and PTP can be VLAN-isolated on a shared NIC **only if** the shared NIC is not a bridge member. The playbook will not allow PTP on a bridge. (Heartbeat over a VLAN on the storage bond is a supported, field-proven layout for 4-NIC nodes — see the networking role's *Heartbeat modes*.)

> **Switch caveat — Advantech EKI-8528-4XFL at 1 GbE fibre.** Firmware **1.00.04 flaps 1000BASE-X links** continuously (multiple NIC families affected, idle links included); **firmware ≥ 1.00.06 (r610) fixes it** — the fix is not enumerated in the vendor release notes. Config survives the upgrade (~80–105 s downtime; previous image stays in the backup slot). If 1 G fibre links flap behind these switches, upgrade firmware **before** suspecting NICs. See TROUBLESHOOTING.md, "Storage / heartbeat 1 GbE fibre links flap."
>
> **NIC note — Intel E823-C (`ice`) at 1 GbE fibre: exonerated.** A flap on this NIC was originally attributed to the `ice` driver; a direct retest on the same blamed NIC showed the flap stop completely (~2.4/min sustained over 78 h → zero) with the switch firmware fix above as the only variable. The `ethtool` mismatch (Supported `1000baseT` vs Advertised `1000baseX`) remains observable but is non-causal for link stability. Prefer 10 GbE for storage/heartbeat on **bandwidth** grounds; it is not required as a stability workaround on fixed switch firmware.

## BMC requirements

- IPMI-over-LAN must be enabled (Dell iDRAC: Connectivity → Network → IPMI Settings; Supermicro: IPMI → Network)
- Dedicated admin user with a strong password used only by STONITH
- IPMI network reachable from the cluster nodes (for `fence_ipmilan`)

## Recommended BIOS / iDRAC settings for RT workloads

Vendor-specific. Capture once per hardware platform.

**Dell PowerEdge / XR series (iDRAC 9):**
- System Profile: Performance (or a custom profile with C-states disabled and turbo max)
- Power Profile: Performance per Watt Optimized (DAPC) — documented working at a reference deployment
- Logical Processor (SMT/Hyper-threading): enabled; the playbook pins to specific threads via `pinned_cpus`
- Virtualization Technology: enabled
- SR-IOV Global Enable: enabled if PCI passthrough is used
- Energy Efficient Turbo: disabled
- C-states: disabled or limited to C1 — deep C-states destroy RT latency
- Memory Operating Mode: Optimizer mode

**Supermicro (for the Windows-hosting node):**
- Advanced → CPU Configuration: Hyper-Threading enabled, Intel VT-x enabled, Intel VT-d enabled
- Advanced → Power & Performance: Performance profile, C-states disabled
- IPMI → Network Configuration: assign OOB IP

## Reference physical topology

A 3-node edge cluster typically sits in a single substation cabinet:

```
+------------------+  +------------------+  +------------------+
|  Node A          |  |  Node B          |  |  Node C          |
|  2U rackmount    |  |  2U rackmount    |  |  1-2U rackmount  |
|  Xeon, 128GB     |  |  Xeon, 128GB     |  |  Xeon, 64-128GB  |
|  3+ NVMe OSD     |  |  3+ NVMe OSD     |  |  3+ NVMe OSD     |
+--+---+---+---+---+  +--+---+---+---+---+  +--+---+---+---+---+
   |   |   |   |         |   |   |   |         |   |   |   |
   |   |   |   +---PTP---+   |   |   +---PTP---+   |   |   +---PTP---> GM
   |   |   +---Heartbeat-----+   +---Heartbeat-----+   |
   |   +---Storage (LACP) --> Storage ToR <-- Storage --+
   +---Mgmt (LACP) ----------> Mgmt ToR <------ Mgmt ---+
                                  |
                                Substation WAN
```

PTP grandmaster: typically an external GPS/Galileo-disciplined time source. The cluster nodes run as PTP slaves. Document the GM source and its redundancy at each site.
