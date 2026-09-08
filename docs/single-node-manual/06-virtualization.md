# 06 — Virtualization

This step starts libvirt/KVM, applies the real-time tuned profile, and reserves memory (hugepages) and CPUs (isolation) for the relay. It sets the values; step 08 reboots into them and adds the remaining kernel-level tuning.

## Enable libvirt

```bash
sudo systemctl enable --now libvirtd
sudo systemctl status libvirtd --no-pager
virsh version
```

If the station-bus libvirt network was deferred in step 05, define it now:

```bash
sudo virsh net-define /tmp/net-station-bus.xml
sudo virsh net-start station-bus
sudo virsh net-autostart station-bus
sudo virsh net-list --all
```

## Apply the real-time host tuned profile

`realtime-virtual-host` is the tuned profile for running RT guests on a KVM host. It configures CPU isolation, disables a range of background jitter sources, and prepares the host for `kernel-rt`.

It is parameterized by a variables file declaring which CPUs to isolate. Isolate the cores the VM will be pinned to plus the emulator cores, and leave the low-numbered cores for the host (housekeeping, interrupts, the OS).

## Plan your core layout

**Every value in the rest of this guide is derived from your CPU's core count.** The examples
below use a 16-core part; a 24-core part produces different numbers everywhere. Work through this
section once, write your numbers in the table at the end, and use *those* — do not copy the
example indices.

### 1. Read your topology

```bash
lscpu | grep -E '^CPU\(s\)|^Core|^Thread|^Socket|^Model name|^L3'
lscpu -e            # per-CPU listing: core, socket, online state
```

Three things to confirm before going further:

| Field | Required | Why |
|---|---|---|
| `Thread(s) per core` | **1** | Hyper-threading must be off (step 01). If this reads 2, stop and disable it in BIOS — a sibling thread shares the physical core with a pinned vCPU and the isolation is not real. |
| `Socket(s)` | 1 preferred | On a two-socket host every isolated core must be on the *same* socket as the guest's memory (step 10 `<numatune>`), or you pay a cross-socket penalty on every access. |
| `CPU(s)` | your total | Call this **N**. With HT off, CPU numbers 0…N−1 are physical cores. |

### 2. Count what the workload needs

The SSC600SW reference profile requires **six cores**:

| Role | Cores | Purpose |
|---|---|---|
| Relay vCPUs | 4 | The guest's virtual CPUs. vCPU 0 runs the relay's OS and WebHMI; vCPUs 1–3 run protection. |
| Emulator / iothread | 1–2 | QEMU's own threads and guest I/O. Must not share with a vCPU. |
| Process-bus IRQs | 1 per NIC | Services the process-bus NIC interrupts. These sit on **housekeeping** cores, not in the isolated block — see below. |

Everything else is **housekeeping**: the host OS, all other device interrupts, your SSH session,
monitoring, and the storage stack. Leave it a real share — **at least 4 cores, and more on a
larger part.** A host starved of housekeeping cores produces latency spikes that look like
isolation failures.

### 3. Allocate from the top down

Take the isolated block from the **high-numbered end** and leave the low numbers to the host.
Within that block, assign in this order:

```
   0 … (N-7)     HOUSEKEEPING        host OS, storage, your shell, and ALL device
                                     interrupts including the process-bus NICs
   ---- isolated block below this line ----
   N-6           spare               headroom; leave it empty
   N-5, N-4      emulator + iothread
   N-3 … N-1  +  the 4th vCPU        relay vCPUs 0-3
```

Pick the process-bus IRQ core(s) from the **top of the housekeeping range** — the highest-numbered
cores that are still outside the isolated block. They are furthest from core 0, which carries the
boot CPU's timer and a share of the host's other work.

Worked on two different parts, to show the indices are *not* portable:

| | 16-core part | 24-core part |
|---|---|---|
| Total cores (N) | 16 | 24 |
| Housekeeping | **0–9** | **0–11** |
| Isolated block | **10–15** | **18–23** |
| &nbsp;&nbsp;emulator + iothread | 10–11 | 18–19 |
| &nbsp;&nbsp;relay vCPUs 0–3 | 12, 13, 14, 15 | 20, 21, 22, 23 |
| &nbsp;&nbsp;RT cache class (`RT_CORES`) | **13–15** | **21–23** |
| Process-bus IRQ core(s) | 9, or 8 and 9 for PRP | 11, or 10 and 11 for PRP |

> **`RT_CORES` is the vCPUs *minus vCPU 0*.** The relay's first vCPU runs its OS and WebHMI; giving
> it the protected cache partition lets that activity evict the protection cores' cache lines. On
> the 16-core part the vCPUs are 12–15 and `RT_CORES` is 13–15.

> **Process-bus interrupts belong on housekeeping cores.** Do not place them inside the isolated
> block. `irqaffinity=` exists to steer device interrupts away from isolated CPUs, and the
> `realtime` tuned profile bans irqbalance from them for the same reason — an isolated core is
> configured for uninterrupted execution, so `nohz_full` disables its timer tick and `rcu_nocbs`
> moves its RCU callbacks elsewhere. An interrupt arriving there forces the tick back on and
> generates the work that isolation was set up to remove. A busy NIC queue on such a core defeats
> the isolation rather than benefiting from it.

> **With two process-bus NICs (PRP), give each its own housekeeping core.** Both LANs carry Sampled
> Values at the same time, so a single shared interrupt core makes them contend under exactly the
> load that matters. Two cores at the top of the housekeeping range cost nothing that the host
> needs elsewhere.

### 4. Derive the values that follow from it

Four later steps need this layout expressed in different forms. Compute them now:

```bash
# Set these to YOUR numbers from the table above, then the rest is derived.
ISOLATED="10-15"          # step 06 (this step) + step 08
VCPUS="12 13 14 15"       # step 10 <vcpupin>
EMULATOR="10-11"          # step 10 <emulatorpin>
RT_CORES="13-15"          # step 09 cache class (vCPUs minus vCPU 0)
IRQ_CORE=9                # step 09 process-bus IRQ core — HOUSEKEEPING, outside ISOLATED
                          # for PRP use two: e.g. IRQ_CORE_A=8 IRQ_CORE_B=9

# CPUMASK for step 09 is a HEX BITMASK, not a core number:
printf 'CPUMASK="%x"\n' $((1 << IRQ_CORE))       # core 9  -> 200
                                                  # core 12 -> 1000
                                                  # core 17 -> 20000

# L3 cache ways, for the step 09 pqos masks:
lscpu | grep -i '^L3'                             # total L3, e.g. 22 MiB
cat /sys/fs/resctrl/info/L3/cbm_mask              # bit count = number of ways
```

**The cache mask is a fraction of the L3, not a quantity of it.** MiB per way = L3 total ÷ ways.
The relay requires **at least 6 MiB**, so ways needed = 6 ÷ MiB-per-way, rounded up:

| Part | L3 | Ways | MiB/way | Ways for 6 MiB | RT mask | Gives |
|---|---|---|---|---|---|---|
| 22 MiB / 11-way | 22 | 11 | 2.00 | 3 | `0x700` | 6.00 MiB |
| 45 MiB / 12-way | 45 | 12 | 3.75 | 2 | `0xc00` | 7.50 MiB |

A mask copied from another machine can land **below** the floor with nothing to report it — the
partition is applied successfully, it is simply too small. Compute it for the part in front of you.

### 5. Write it down

Fill this in and keep it beside you for steps 08, 09, 10 and 12 — all four must agree:

```
  My CPU: ____________________  cores N = ____  L3 = ____ MiB / ____ ways

  housekeeping   0 - ____         RT_CORES         ____ - ____
  isolated    ____ - ____         RT cache mask    0x______ = ____ MiB
  emulator    ____ - ____         non-RT mask      0x______
  vCPUs 0-3   ____________

  process-bus NIC 1  ____ on core ____  -> mask ______
  process-bus NIC 2  ____ on core ____  -> mask ______     (PRP only)
        both cores must be in the HOUSEKEEPING range above
```

Set the isolated set in the tuned variables file (replace the range with the chosen cores):

```bash
sudo tee /etc/tuned/realtime-virtual-host-variables.conf >/dev/null <<'EOF'
# Cores handed to the RT guest (vCPUs + emulator). Adjust to YOUR topology.
# These cores are removed from the kernel's general scheduling and IRQ
# balancing so the guest owns them.
isolated_cores=10-15

# Leave isolated CPUs out of the managed IRQ set so device interrupts do
# not land on the guest's cores.
isolate_managed_irq=Y
EOF

sudo tuned-adm profile realtime-virtual-host
tuned-adm active
```

> Record the `isolated_cores` value. It is reused three times: the VM's `<vcpupin>`/`<emulatorpin>` (step 10), the L3 cache partitioning (step 08), and the validation checks (step 12). All three must agree.

> `isolate_managed_irq=Y` keeps kernel-managed IRQs off the isolated cores, but **only at device probe time**. A later change to a NIC's queues or rings re-spreads those IRQs across all CPUs again, so step 08 adds an explicit re-pin of the process-bus NIC IRQs to the housekeeping cores. This setting is necessary but not sufficient on its own.

## Reserve 1 GiB hugepages

The SSC600SW backs its memory with **1 GiB hugepages**, locked so it does not swap. Reserve enough whole 1 GiB pages for the guest's memory (8 GiB → 8 pages) plus any additional RT VM.

1 GiB hugepages must be reserved at boot via the kernel command line; runtime allocation of 1 GiB pages is unreliable once memory is fragmented. Set the kernel parameters (this also previews step 08, which adds the RT parameters):

```bash
# Reserve eight 1 GiB hugepages and make 1 GiB the default huge size.
sudo grubby --update-kernel=ALL --args="default_hugepagesz=1G hugepagesz=1G hugepages=8"
```

> Verify the memory budget. `hugepages=8` with `hugepagesz=1G` reserves **8 GiB** of RAM at boot; it is not lazily allocated. Ensure the host has that 8 GiB available in addition to its own requirements. Reserving more than is available will prevent the host from booting correctly.

The reservation takes effect after the reboot in step 08. Confirm it then with `grep Huge /proc/meminfo`.

## Confirm KVM is healthy

```bash
virt-host-validate qemu
```

Expect mostly `PASS`. Warnings about IOMMU apply only to PCI passthrough; the SSC600SW reference uses macvtap and virtio, not PCI passthrough, so an IOMMU warning is acceptable here. A `FAIL` on hardware virtualization means VT-x is disabled in the BIOS.

Continue to [07 — Time synchronization (PTP)](07-time-sync-ptp.md).
