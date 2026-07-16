# 13 — Troubleshooting

Entries are organized by symptom. Most issues trace back to a value that must agree across several steps; confirm that the isolated cores, VM pinning, and cache assignment all name the same cores.

## PTP alternates between SLAVE and UNCALIBRATED

**Cause:** a process other than `ptp4l` is consuming inbound frames on the PTP NIC. The common case is a VM macvtap passthrough and the host `ptp4l` on the same NIC. macvtap delivers the NIC's frames to the guest, so the host `ptp4l` rarely receives a Sync/Announce message and the clock does not settle.

**Fix:** keep PTP on a dedicated NIC (step 05). The process-bus macvtap must be on a different NIC (`ens2f0`), not the PTP NIC (`ens2f1`). Verify nothing else is bound to the PTP interface:

```bash
sudo virsh domiflist ssc600-01     # the macvtap 'direct' source must NOT be the PTP NIC
ip -br link                        # PTP NIC must not be enslaved to any bridge
```

## PTP never sees the grandmaster

**Cause:** NIC hardware. Some adapters do not correctly provide PTP hardware timestamps, or the port is cabled to a segment with no grandmaster.

**Fix:** confirm hardware timestamping and the link:

```bash
sudo ethtool -T ens2f1     # PTP Hardware Clock present? hardware rx/tx timestamping?
sudo ethtool ens2f1        # Link detected: yes
```

If timestamping is software-only or the PHC is missing, move PTP to a NIC with a hardware PHC. If the link is down or on the wrong segment, correct the cabling/VLAN.

## The host and relay share an IP address

**Cause:** the station-bus bridge was assigned the same IP as the SSC600 appliance. The host answers ARP for the relay's address, and the relay becomes unreachable.

**Fix:** the bridge's host address and the relay's address must be different addresses in the same subnet. Remove the conflicting address from the bridge:

```bash
ip -br addr show station-bus
sudo nmcli connection modify station-bus ipv4.addresses <host-addr>/24   # NOT the relay's
sudo nmcli connection up station-bus
```

## A bridge enslaved the wrong NICs

**Cause:** a bridge was created enslaving more interfaces than intended, including management or other critical NICs.

**Fix:** the station-bus bridge must enslave only the station-bus port. Check for and detach any others:

```bash
bridge link show
sudo ip link set <wrong-nic> nomaster      # remove a NIC mistakenly enslaved
```

Then reassert the intended membership with `nmcli` (step 05).

## Hugepages disappear after a reboot

**Cause:** 1 GiB hugepages were reserved at runtime (`sysctl vm.nr_hugepages=...`) instead of on the kernel command line. Runtime reservation of 1 GiB pages does not survive reboot and often cannot satisfy the request once memory is fragmented, so the VM fails to start after a power event.

**Fix:** reserve them on the kernel cmdline (step 06/08) so they exist from boot:

```bash
cat /proc/cmdline | tr ' ' '\n' | grep hugepages   # default_hugepagesz=1G hugepagesz=1G hugepages=N
grep Huge /proc/meminfo
```

If the count is wrong, re-apply with `grubby --update-kernel=ALL --args="..."` and reboot.

## The VM will not start — `virsh start` errors

Review the error. Common causes:

- **`unable to map backing store ... hugepages`** — insufficient free 1 GiB hugepages. Check `grep Huge /proc/meminfo`; the reservation may be smaller than the VM requests, or another VM took them.
- **`cpuset ... not a valid CPU`** — a `<vcpupin>` names a core that does not exist or is offline. Match the pins to `lscpu -e`.
- **`Network not found: station-bus`** — the libvirt network is not defined/started (step 05/06).
- **`unable to access ... ssc600-01.img`** — wrong path, or SELinux label. Run `sudo restorecon -v` on the image and confirm ownership (`qemu:qemu`).

## cyclictest Max latency is too high

Check each of the following jitter sources:

1. **Hyper-threading still enabled** — a sibling thread shares the core. Disable HT in BIOS (step 01).
2. **C-states / Turbo enabled** — re-clocking and deep idle add latency. Disable in BIOS (step 01).
3. **Governor not `performance`** — `cpupower frequency-info`; re-set it (step 08) (if CPU throtteling via the OS is not completely disabled on BIOS level).
4. **Isolation mismatch** — the measured cores are not the isolated/pinned cores. Make `isolcpus`, `<vcpupin>`, `RT_CORES`, and the cyclictest `-a` range name the same cores.
5. **RT throttle still on** — `sysctl kernel.sched_rt_runtime_us` should be `-1`.
6. **`irqbalance` running** — it can move IRQs onto isolated cores; ensure it is inactive and `irqaffinity` is set (step 08).
7. **A NIC IRQ landed on an isolated core** — see the dedicated entry below. This is the usual cause of a run that is clean for a long time and then spikes once traffic is heavy.
8. **vhost-net threads unpinned** — under heavy GOOSE/SV they steal cycles; confirm they are pinned (step 11).

## cyclictest is clean for hours, then spikes to milliseconds

**Cause:** a process-bus NIC's RX interrupt is being serviced on an **isolated** core, so heavy GOOSE/Sampled-Value multicast injects jitter into the relay's cores. `isolate_managed_irq=Y` only keeps managed IRQs off the isolated cores **at device probe time**; a later queue/ring change — `ethtool -L`/`-G`, an interface down/up, or tuned's `netdev_queue_count` — recreates the queues and re-spreads the IRQs across all CPUs, including isolated ones. `irqbalance` cannot move a managed IRQ back, so disabling it is not enough on its own.

**Diagnose:**

```bash
cat /sys/devices/system/cpu/isolated        # the isolated set, e.g. 10-15
for nic in ens2f0 ens2f1; do
  for irq in /sys/class/net/$nic/device/msi_irqs/*; do
    i=$(basename "$irq"); echo "$nic irq$i -> $(cat /proc/irq/$i/effective_affinity_list)"
  done
done
# Any affinity inside the isolated set is the fault.
```

**Fix:** re-pin those IRQs to the housekeeping cores and make it persistent so it re-applies after the tuned profile and any NIC change (step 08, "Pin the process-bus NIC IRQs to the housekeeping cores").

## `pqos` reports nothing / cache partition has no effect

**Cause:** `/sys/fs/resctrl` is not mounted. `pqos -e`/`-a` have no effect without it.

**Fix:** confirm the mount and the setup service:

```bash
mount | grep resctrl                        # must be mounted (step 08)
sudo systemctl status sys-fs-resctrl.mount
sudo systemctl status vpac-ssc600-setup.service
sudo pqos -s                                # the RT cache class should be present
```

On CPUs without Intel CAT, this is expected: cache partitioning is unavailable and the relay shares L3. This is acceptable and not a failure.

## Relay lost its license / config after a disk swap

**Cause:** the disk image was restored to the pristine factory copy. License activation and all in-VM configuration are stored on the disk.

**Fix:** restore the post-licensing backup from step 11, not the factory image. If only the factory image is available, re-activate via PCM600 and re-import the configuration.

---

If a symptom is not listed here, check `journalctl -xe`, `sudo virsh dumpxml ssc600-01` (compare the live domain to the intended XML), and `dmesg` for RCU stalls or hung tasks on the isolated cores. The general project [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) and [OPERATIONS.md](../OPERATIONS.md) cover the broader stack.


## L3 Cache Partitioning with Intel Cache Allocation Technology (CAT)

### Prerequisites

- CPU must support Intel CAT (check with `pqos -d` or `cpuid`)
- `intel-cmt-cat` package installed
- Root access

### Step 1: Determine available cache ways

```bash
pqos -s
```

Look at the default COS0 mask. This is the bitmask of all available LLC ways. For example, `0x7ff` means 11 ways (bits 0-10).

### Step 2: Determine per-way cache size

```bash
cat /sys/devices/system/cpu/cpu0/cache/index3/ways_of_associativity
cat /sys/devices/system/cpu/cpu0/cache/index3/size
```

Divide total L3 size by the number of ways to get the size per way. For example, 22 MiB / 11 ways = 2 MiB per way.

### Step 3: Calculate required bitmasks

Partition the available ways into non-overlapping contiguous bitmasks. Each bit corresponds to one cache way. The number of bits set determines the cache size allocated (bits set x per-way size).

Example for an 11-way (0x7ff) cache at 2 MiB/way, targeting a 6 MiB isolated partition (as required by ABB SSC600SW):

| Partition | Ways needed | Bits | Mask |
|-----------|-------------|------|------|
| General (16 MiB) | 8 | 0-7 | `0x0ff` |
| Isolated (6 MiB) | 3 | 8-10 | `0x700` |

To compute a mask: for ways M through N, the mask is `((1 << (N - M + 1)) - 1) << M`.

**Constraints:**

- Masks must not exceed the available ways (must be a subset of the default COS0 mask)
- Masks should be non-overlapping for true isolation
- Masks must have contiguous bits set (hardware requirement on most platforms)

### Step 4: Apply configuration

```bash
pqos -R                                        # reset to defaults
pqos -e "llc:0=<mask0>;llc:1=<mask1>"          # set COS bitmasks
pqos -a "llc:1=<core_list>"                    # assign cores to COS
```

All cores default to COS0 after reset. Only reassign cores that belong to non-default partitions.

### Step 5: Verify

```bash
pqos -s
```

Confirm each COS shows the expected mask and core assignments.

### Samples
For an Intel Xeon Gold 6226R CPU @ 2.90GHz (1 Socket, 16 cores) with 22MB Cache Size, vCPUs of SSC600SW on cores 12-15 and the L3 cache of 6MB for cores 13-15:
  pqos -e "llc:0=0x1ff;llc:1=0x600"
  pqos -a "llc:1=13,14,15"