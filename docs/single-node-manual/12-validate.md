# 12 — Validate

A relay that boots does not necessarily run deterministically. These checks confirm the real-time guarantees hold. Run them after the VM is running and the host has settled.

These are the single-node subset of the checks the `validate` role performs on the three-node cluster, excluding the Ceph, Pacemaker, and STONITH checks, which do not apply.

## Real-time kernel and isolation

```bash
uname -r                                   # ...rt... kernel
cat /sys/devices/system/cpu/isolated       # the isolated set, e.g. 10-15
tuned-adm active                           # realtime-virtual-host
cat /proc/cmdline | tr ' ' '\n' | grep -E 'isolcpus|nohz_full|rcu_nocbs|hugepages'
cat /sys/kernel/debug/sched/rt_runtime_us 2>/dev/null || sysctl kernel.sched_rt_runtime_us  # -1
```

All six isolated cores (`10-15` in the example) should be absent from the general scheduler. The four vCPU cores (`RT_CORES`, `12-15`) should appear in the RT cache class (`pqos -s`).

## Latency

`cyclictest` measures scheduling latency on the isolated cores. Run it on the isolated cores while the VM is running, so the measurement reflects operating conditions:

```bash
# Measure on the isolated cores (adjust the range to yours), 5-minute run.
sudo cyclictest -m -p 95 -t1 -a 10-15 -i 200 -D 5m
```

Read the **Max** latency. For a protection host the target is below approximately **100 µs** worst-case (the architecture pattern's real-time guarantee). A Max in the low tens of microseconds is acceptable; a Max in the hundreds indicates contention on the cores. Review isolation (step 08), the governor, C-states (BIOS, step 01), and confirm hyper-threading is disabled.

> Run cyclictest **with the relay running and process-bus traffic flowing**, for long enough to catch intermittent spikes — an idle, short run hides the most important failure mode (a periodic event landing on an isolated core). A clean run for minutes that then spikes to milliseconds points at the device-IRQ and helper-thread checks below, not at the kernel tuning above.

## Device IRQs and helper threads

These are the runtime sources that pass every static check above and still produce occasional millisecond spikes once real traffic flows.

```bash
# 1. irqbalance must be inactive (it re-spreads IRQs onto isolated cores):
systemctl is-active irqbalance        # inactive

# 2. No process-bus NIC IRQ may sit on an isolated core. isolate_managed_irq=Y
#    only holds at probe time; a queue/ring change (step 08) can re-spread them.
#    Each line's affinity must be a HOUSEKEEPING core, never an isolated one:
for nic in ens2f0 ens2f1; do          # process-bus (and PTP) NICs
  for irq in /sys/class/net/$nic/device/msi_irqs/*; do
    i=$(basename "$irq")
    echo "$nic irq$i -> $(cat /proc/irq/$i/effective_affinity_list)"
  done
done

# 3. The relay's vhost-net threads must be pinned (step 11), not on a vCPU core:
qpid=$(pgrep -f 'guest=ssc600-01,')
for tid in $(pgrep "vhost-$qpid"); do
  echo "vhost $tid -> $(taskset -pc "$tid" | grep -o '[0-9,-]*$') $(chrt -p "$tid" | tail -1)"
done
```

Cross-check the IRQ affinities against `cat /sys/devices/system/cpu/isolated`: any device IRQ whose effective affinity falls inside the isolated set will inject jitter into the relay and must be re-pinned to a housekeeping core (step 08).

## Memory

```bash
grep Huge /proc/meminfo
# HugePages_Total should equal the reservation (e.g. 8 of 1 GiB);
# HugePages_Free should have dropped by the VM's share while it runs.

# No swap activity for the locked guest:
vmstat 1 3        # si and so columns should stay 0
```

## Time synchronization

```bash
# PTP offset from the grandmaster on the dedicated NIC:
sudo pmc -u -b 0 'GET CURRENT_DATA_SET' -i ens2f1 | grep offsetFromMaster
# Should be below one microsecond and stable.

# Disciplined system clock (timemaster/chrony path):
chronyc tracking        # Leap status: Normal, small Last offset
```

The PTP port should be in `SLAVE` state and should not alternate to `UNCALIBRATED`. Alternation generally indicates another process is using the PTP NIC (step 13).

Confirm the relay can see host PTP health through the share:

```bash
cat /var/lib/libvirt/ptp-status/status     # offsetFromMaster + leap, refreshed every few seconds
```

## The VM itself

```bash
sudo virsh dominfo ssc600-01               # running, autostart enabled
sudo virsh vcpuinfo ssc600-01              # each vCPU pinned to its assigned core as in the XML

# Confirm the RT XML invariants rendered into the live domain:
sudo virsh dumpxml ssc600-01 | grep -E 'hugepages|locked|nosharepages|vcpusched|pmu|memballoon|watchdog'
# Expect: 1 GiB hugepages, <locked/>, <nosharepages/>, vcpusched fifo priority 50,
#         pmu state='off', memballoon model='none', watchdog action='none'.
```

## The network paths

```bash
# Station bus — host can reach the relay's HMI address:
ping -c3 <relay-station-bus-ip>

# Process bus — confirm the macvtap is attached and the NIC carries no host IP:
sudo virsh domiflist ssc600-01             # one interface of type 'direct' (macvtap)
ip -br addr show ens2f0                     # process-bus NIC: UP, still NO host IP
```

Confirmation of the GOOSE/SV path is performed from the protection side (PCM600 or the relay's diagnostics showing receipt of subscribed GOOSE and Sampled Values) rather than from RHEL; the frames do not surface to the host by design.

## Completion criteria

The deployment is complete when:

- `uname -r` is the RT kernel and the cores are isolated
- `cyclictest` Max is under ~100 µs **sustained while the VM runs and traffic flows** (not just an idle snapshot)
- `irqbalance` is inactive, no process-bus NIC IRQ is on an isolated core, and the vhost-net threads are pinned off the vCPU cores
- hugepages are reserved and the guest's are locked, no swap
- PTP is `SLAVE`, sub-microsecond, not alternating
- the VM is running, autostarting, pinned, with every RT XML invariant live
- host↔relay station bus works and the process-bus NIC has no host IP

If any of these conditions is not met, [13 — Troubleshooting](13-troubleshooting.md) maps the symptom to the cause.
