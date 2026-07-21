# Single-node manual deployment

This guide builds a **single-node Virtual Protection host** on Red Hat Enterprise Linux 9 **by hand** — no Ansible, no playbooks. Each command is run directly, on one machine, producing a real-time-tuned KVM host running an **ABB SSC600SW** protection relay as a virtual machine.

Its purposes:

1. **Document the automation.** Each operation `ansible-vpac` performs on a node is performed here by hand, so the purpose of each role is explicit.
2. **Serve the single-node topology.** The [architecture pattern](https://github.com/RedHatEdge/virtual-protection) defines a single-node variant alongside the three-node cluster. This guide builds it.
3. **Stand alone.** A site running a small proof-of-concept can follow this guide without adopting Ansible.

## What you are building

```
                          ┌──────────────────────────────────────────┐
                          │  RHEL 9 host (real-time tuned)           │
                          │                                          │
   station bus  ──bridge──┤  ┌────────────────────────────────┐      │
                          │  │  ABB SSC600SW VM               │      │
   process bus ──macvtap──┤  │  4 vCPU pinned · FIFO prio 50  │      │
                          │  │  1 GiB hugepages · locked mem  │      │
   PTP NIC ───────────────┤  │  virtio disk + virtio NICs     │      │
   (dedicated)            │  └────────────────────────────────┘      │
                          │                                          │
   management ────────────┤  libvirt / KVM · tuned realtime-host     │
                          │  ptp4l on dedicated NIC · local storage  │
                          └──────────────────────────────────────────┘
```

One host. One relay VM. **Local storage** — no Ceph. **No Pacemaker, no corosync, no STONITH** — there is no cluster to coordinate or fence. The single-node variant comprises only the host-tuning and virtualization layers required to run a relay deterministically on RHEL.

> **PRP variant:** for a redundant process bus (IEC 62439-3, relay as DANP), the host presents **two** process-bus NICs on two independent LANs — 5 NICs total — and the relay does the duplicate-discard. Carried as callouts in steps 01, 05, 10, and 12.

For the highly-available three-node version, use the automated path — see [`../DEPLOYMENT-CONNECTED.md`](../DEPLOYMENT-CONNECTED.md) and [`../DEPLOYMENT-AIRGAPPED.md`](../DEPLOYMENT-AIRGAPPED.md).

## Connected and air-gapped

The main path of this guide assumes a **connected** host — one that can reach Red Hat's CDN and the package repositories directly. Where an **air-gapped** site differs, the difference is given in a callout box:

> **Air-gapped variant**
> The procedure for a host that cannot reach the internet — point at a local Red Hat Satellite, mirror, or registry.

From host tuning onward the procedure is identical on both paths; only how packages and the vendor bundle arrive changes.

## The steps

| # | Step | Purpose |
|---|------|---------|
| 01 | [Prerequisites](01-prerequisites.md) | Hardware, BIOS, the four-NIC layout, the ABB bundle, licensing tools |
| 02 | [Install RHEL](02-install-rhel.md) | Base RHEL 9 install and first boot |
| 03 | [Register and enable repos](03-register-and-repos.md) | Subscription, repositories (connected + air-gapped) |
| 04 | [Host baseline](04-host-baseline.md) | Hostname, packages, firewall, base time sync |
| 05 | [Networking](05-networking.md) | Station-bus bridge, process-bus reservation, dedicated PTP NIC |
| 06 | [Virtualization](06-virtualization.md) | libvirt/KVM, TuneD real-time profile, hugepages, isolated CPUs |
| 07 | [Time synchronization (PTP)](07-time-sync-ptp.md) | ptp4l on the dedicated NIC, phc2sys, drop NTP |
| 08 | [Real-time tuning](08-rt-tuning.md) | Kernel cmdline, scheduler, governor, L3 cache, disable jitter services, pin NIC IRQs |
| 09 | [Prepare the SSC600 bundle](09-prepare-ssc600-bundle.md) | Extract the disk image, host setup script, PTP share |
| 10 | [Define the SSC600 domain](10-define-ssc600-domain.md) | The annotated libvirt XML |
| 11 | [Start and license](11-start-and-license.md) | Boot the VM, pin vhost-net threads, autostart, web HMI, PCM600 activation |
| 12 | [Validate](12-validate.md) | cyclictest under load, device-IRQ/jitter checks, PTP offset, VM health, GOOSE/SV path |
| 13 | [Troubleshooting](13-troubleshooting.md) | Symptoms and causes |

Work through the steps in order. Real-time tuning depends on the kernel and isolation being in place, and the VM depends on networking, hugepages, and the cache controller being ready before it starts. Performing steps out of order produces a VM that boots but does not meet latency targets.

## Conventions

- Commands are run as a normal user with `sudo`, or as `root` where noted.
- Placeholders are written as `<this>` — replace them with site values.
- Example interface names (`ens1f0`), IP ranges (`10.0.0.0/24`), and core numbers are illustrative. Derive site-specific values from the hardware (the prerequisites step describes how).
