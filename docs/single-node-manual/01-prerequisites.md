# 01 — Prerequisites

Put the hardware, the network layout, and the vendor materials in place before installing.

## Hardware

A single Red Hat-certified x86_64 server. Real-time behavior, not raw capacity, is the priority:

- **CPU** with enough cores to *isolate* a block for the VM and still leave housekeeping cores for the host. The SSC600SW reference profile pins **4 vCPUs + emulator threads**, so a minimum of 6–8 physical cores is required; more provides additional headroom. System requirements typically increase with number of protected bays per IED. Hyper-threading **disabled** (see BIOS below).
- **RAM** sized for the host plus 1 GiB-hugepage backing for the guest. The SSC600SW uses 8 GiB of locked hugepages; budget that amount in addition to host memory and a margin.
- **Storage** — local disk only. Single-node uses local storage, not Ceph. A single SSD/NVMe with room for the OS and the ~30 GiB relay disk image is sufficient.
- **Intel CPU with CAT** (Cache Allocation Technology / RDT) for L3 cache partitioning of the relay — the SSC600SW host setup uses it. Most modern Xeon parts include it. Non-Intel or no-CAT hardware also works; the cache-partitioning step is skipped.

## BIOS / firmware settings

These are required for deterministic latency. Set them before installing:

| Setting | Value | Reason |
|---|---|---|
| System Profile | Performance / Max Performance | Prevents the firmware from re-clocking under its own policy |
| C-States | Disabled | Deep idle states add wake-up latency |
| Turbo / SpeedStep | Disabled (or fixed) | Frequency transitions introduce jitter |
| Hyper-Threading | Disabled | A sibling thread shares core resources with a pinned vCPU |
| VT-x / VT-d | Enabled | Hardware virtualization + IOMMU for passthrough |
| Memory Patrol Scrub | Disabled | Background scrubbing introduces unpredictable memory-access stalls |
| USB Legacy / SMI | Disabled where possible | System Management Interrupts cause latency spikes |

## The four-NIC network layout

A Virtual Protection host separates traffic by purpose. The single-node minimum is **four NICs**, each with one function:

| Role | Example name | Carries | Notes |
|---|---|---|---|
| **Management** | `ens1f0` | SSH, host admin, the libvirt API | Administrative access |
| **Station bus** | `ens1f1` | IEC 61850 MMS, engineering tools, the relay's web HMI | Bridged into the VM |
| **Process bus** | `ens2f0` | GOOSE / Sampled Values (sub-millisecond protection signaling) | Reserved for the VM via macvtap — **no host IP** |
| **PTP (dedicated)** | `ens2f1` | PTP time sync only | **No other traffic on this NIC** |

Two requirements govern this layout:

- **The PTP NIC is dedicated.** Do not bridge it, attach a VM macvtap to it, or run other traffic over it. If a VM's macvtap and the host's `ptp4l` share a NIC, the macvtap consumes the inbound PTP frames and the host clock does not lock. Step 05 and step 07 enforce this.
- **The process-bus NIC is reserved for the VM**, attached by macvtap passthrough so GOOSE/SV frames go directly to the relay. It gets no host IP and no bridge.

The example names (`ens1f0`, `ens2f1`) are illustrative; site names will differ. After install, run `ip -br link` and map each physical port to its role based on the cabling.

## The ABB SSC600SW bundle

ABB ships the SSC600SW as a **KVM software bundle**, not a generic OVA. Required items:

- **`SSC600_SW_KVM-<version>.cab`** — the bundle (e.g. `SSC600_SW_KVM-1.5.1.cab`). It contains a gzip-compressed **raw disk image** (`ssc600_disk.img.gz`, ~30 GiB uncompressed). Because it is already a raw KVM image, no OVA-to-KVM conversion is required — extract and run it. (Some vendor appliances are delivered as VMware OVAs and do require conversion; this one does not.)
- A **license** for the instance. SSC600SW licensing is **per-VM** and is activated *after* the VM is running, using ABB's tools; it is not configured beforehand. See below.

Stage the `.cab` on the host (or on the local mirror, for air-gapped sites) before step 09.

## ABB tooling (for licensing and configuration)

The relay's protection configuration and license activation are performed with **ABB PCM600** and the SSC600SW's **web HMI**, not from RHEL. Required:

- A **Windows machine** with **PCM600** installed that can reach the relay over the station bus.
- Network reachability from PCM600 / a browser to the SSC600SW's station-bus address.

This guide brings the VM up and makes it reachable. The protection engineering inside it (settings, SCADA bindings, license activation) is ABB's workflow and is summarized in step 11.

> **Air-gapped variant**
> Only the delivery of the `.cab` and the RPMs changes for air-gapped sites. Stage `SSC600_SW_KVM-<version>.cab` on removable media or an internal file server, and ensure the Satellite / mirror / local registry is reachable from the host. The PCM600 workstation is on the station-bus side in both cases.

When the hardware is racked, the BIOS is configured, the four NICs are cabled to the correct switches/VLANs, and the `.cab` is staged, continue to [02 — Install RHEL](02-install-rhel.md).
