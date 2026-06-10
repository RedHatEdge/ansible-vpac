# Image-mode architecture

How the single-node vPAC host is built and deployed when the operating system is
an image-mode (bootc) container image. All diagrams render natively on GitHub.

For the prose walkthrough see [`README.md`](README.md) (image) and
[`runtime/README.md`](runtime/README.md) (per-node steps).

---

## 1. The core idea: one generic image, per-site variables

The OS image is **identical for every site**. Everything that makes a node
site-specific is applied *after boot* by Ansible from a per-site variable file.
The image carries tools and tuning; it never carries identity.

```mermaid
flowchart LR
    subgraph BUILD["Build time — baked into the image (identical everywhere)"]
        direction TB
        K["kernel-rt + RT/isolation/hugepage kargs"]
        P["KVM-host packages<br/>qemu-kvm, libvirt, virtiofsd, swtpm, OVMF"]
        T["realtime-virtual-host tuned profile<br/>resctrl mount, performance governor"]
        N["nmstate + NetworkManager (the tools only)"]
        S["linuxptp + chrony (packages + units)"]
    end

    subgraph RUN["Run time — applied per node after boot (site-specific)"]
        direction TB
        H["hostname + network identity<br/>bridges, VLANs, IPs, dedicated PTP NIC"]
        C["isolated-core indices matched to the real CPU topology"]
        PT["device-specific ptp4l / timemaster config"]
        V["the relay VM — vendor disk + libvirt domain"]
    end

    IMG(["vpac-node bootc image"])
    VARS(["per-site var file"])

    BUILD --> IMG
    IMG --> RUN
    VARS --> RUN
```

> The same image and the same pattern scale to a 3-node cluster — the variable
> set just grows (bonds, storage net, Ceph, Pacemaker). Single-node is the
> minimal end of that spectrum.

---

## 2. Build and boot pipeline

`build.sh` builds the image with `podman`, pushes it to a registry, then prints
the `bootc-image-builder` recipe that turns it into bootable media. Two paths —
connected and air-gapped — differ only in where the base image and packages come
from.

```mermaid
flowchart TD
    CF["Containerfile<br/>+ kargs.d/ + files/"] --> BUILD

    subgraph PATHS["Source of base image + packages"]
        direction LR
        CON["Connected<br/>registry.redhat.io + RHSM"]
        AIR["Air-gapped<br/>mirrored registry + local repos<br/>REPO_BASEURL build-arg"]
    end

    PATHS --> BUILD["podman build (build.sh)"]
    BUILD --> REG(["vpac-node image in a registry"])
    REG --> BIB["bootc-image-builder<br/>bib/config.toml"]
    BIB --> MEDIA(["ISO / qcow2 / raw"])
    MEDIA --> BOOT["Boot the node<br/>transactional OS, bootc upgrade + rollback"]
    BOOT --> RUNTIME["Runtime configuration<br/>(see diagram 4)"]
```

`bib/config.toml` carries the install-time admin user and filesystem layout.
After boot, OS updates are `bootc upgrade` (atomic, with rollback) instead of a
playbook re-run.

---

## 3. On-node runtime topology

What a booted, fully-configured single-node host looks like. The relay VM is
pinned to isolated cores and backed by 1 GiB hugepages; its virtual NICs attach
to libvirt **logical networks** so the domain XML never names a physical
interface. The PTP NIC is dedicated and host-only — never shared with a VM
bridge.

```mermaid
flowchart TB
    subgraph HOST["RT KVM host (bootc OS)"]
        direction TB
        RT["kernel-rt · isolated cores · 1 GiB hugepages<br/>realtime-virtual-host tuned · resctrl · perf governor"]
        PTPD["ptp4l / timemaster<br/>(authoritative time; NTP sources removed)"]

        subgraph VM["Relay VM — ssc600 profile"]
            direction TB
            VCPU["vCPUs pinned to isolated cores · SCHED_FIFO<br/>locked hugepage memory · no memballoon · no watchdog"]
            VNIC1(["vNIC: station-bus"])
            VNIC2(["vNIC: process-bus"])
            VFS["virtiofs PTP-status share"]
        end

        subgraph LNET["libvirt logical networks"]
            direction TB
            LSTAT["station-bus (bridge)"]
            LPROC["process-bus (macvtap)"]
            LMGMT["mgmt (bridge, optional)"]
        end

        BRMGMT["br-mgmt<br/>host IP lives here"]
        BRSTAT["br-station<br/>host holds segment addr"]
    end

    subgraph PHY["Physical NICs to the substation"]
        direction TB
        NMGMT["mgmt NIC"]
        NSTAT["station-bus NIC"]
        NPROC["process-bus NIC<br/>reserved · UP · no host IP"]
        NPTP["PTP NIC (dedicated)"]
    end

    VNIC1 --> LSTAT --> BRSTAT --> NSTAT
    VNIC2 --> LPROC --> NPROC
    LMGMT --> BRMGMT --> NMGMT
    PTPD --> NPTP
    VFS -.PTP status.-> VCPU

    NSTAT --- MMS["MMS / HMI segment"]
    NPROC --- GOOSE["GOOSE / Sampled-Value segment"]
    NPTP --- GM["PTP grandmaster"]
```

| Plane | Host side | What attaches |
|---|---|---|
| Management | `br-mgmt` bridge holds the host IP | optional VM mgmt NIC |
| Station bus | `br-station` bridge, host holds the segment address | relay (MMS / HMI) |
| Process bus | NIC reserved, UP, no host IP | relay via **macvtap** (GOOSE / SV) |
| PTP | dedicated NIC with `ptp4l`, host-only | nothing — never a VM bridge |

---

## 4. Runtime deploy sequence

The order a node is brought from first boot to a running, validated relay.

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant Node as Booted host
    participant NM as nmstate / NetworkManager
    participant LV as libvirt
    participant VM as Relay VM

    Op->>Node: bootc kargs — set isolated cores + hugepages to real topology
    Note over Op,Node: reboot to apply kernel args

    Op->>NM: apply-networking.yml + site-vars.yml
    NM->>Node: bridges, reserved process-bus NIC, dedicated PTP NIC
    NM->>LV: define logical networks (station-bus, process-bus, mgmt)
    Note over NM: nmstate auto-rolls back if not confirmed

    Op->>Node: lay down ptp4l / timemaster config (remove NTP)

    Op->>LV: deploy-relay.yml + relay-catalog.yml
    LV->>VM: vm_templates renders RT XML, then vm_deploy defines the domain
    Op->>VM: start once the vendor disk is confirmed

    Op->>Node: validate — uname -r (rt), isolated set, hugepages,<br/>governor, resctrl, tuned, virt-host-validate, cyclictest
```

---

## 5. Where the pieces live

```mermaid
flowchart LR
    ROOT["image-mode/"] --> CF["Containerfile"]
    ROOT --> KA["kargs.d/ — baked RT/isolation/hugepage args"]
    ROOT --> FI["files/ — sysctl, tuned vars, resctrl mount, governor"]
    ROOT --> BIB["bib/config.toml — installer layout + admin user"]
    ROOT --> BS["build.sh — build, push, bootc-image-builder recipe"]
    ROOT --> RUN["runtime/"]
    RUN --> NETW["networking/ — nmstate play + per-site vars"]
    RUN --> WL["workload/ — relay deploy (vm_templates + vm_deploy)"]
```

Static OS config is baked under `kargs.d/`, `files/`, and the `Containerfile`.
Everything site-specific is a variable file consumed by the plays under
`runtime/`.
