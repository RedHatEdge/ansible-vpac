# 08 — Real-time tuning

This step configures the host as a real-time host. It boots the `kernel-rt` kernel, confirms the kernel command line (CPU isolation applied by tuned, hugepages from step 06), adjusts the RT throttle, fixes the CPU governor, and mounts the cache controller. After the final reboot, the host is ready for the relay.

## Boot the real-time kernel

`kernel-rt` was installed in step 04. Make it the default and confirm tuned's RT profile is active:

```bash
# realtime-virtual-host (set in step 06) selects kernel-rt automatically on
# reboot if it's installed; make it explicit and current:
sudo grubby --info=ALL | grep -E 'kernel|rt' | head

# Ensure the rt kernel is the default. Select the newest rt kernel explicitly;
# a bare glob can match several entries after updates.
sudo grubby --set-default "$(ls -1 /boot/vmlinuz-*rt* | sort -V | tail -1)"
```

## Confirm the kernel command line

The `realtime-virtual-host` profile generates the isolation and scheduling kernel arguments from the `isolated_cores` value set in step 06 — `isolcpus`, `nohz_full`, `rcu_nocbs`, `skew_tick`, `intel_pstate=disable`, and the housekeeping IRQ affinity. tuned applies them to the kernel command line on the reboot at the end of this step.

Do **not** add these arguments again with `grubby`. A second copy produces a duplicate `isolcpus=` entry that the kernel may resolve unpredictably. To change which cores are isolated, edit `isolated_cores` in `/etc/tuned/realtime-virtual-host-variables.conf` (step 06) and re-apply the profile — not the kernel command line.

The one cmdline item tuned does not manage is the **1 GiB hugepage reservation**, set in step 06. Confirm it is present, and re-apply only if it is missing:

```bash
# Isolation args (from tuned) and the hugepage reservation (from step 06):
sudo grubby --info=DEFAULT | tr ' ' '\n' | grep -E 'isolcpus|nohz_full|hugepage' || true

# Re-apply the hugepage reservation only if it is absent (idempotent):
sudo grubby --update-kernel=ALL \
  --args="default_hugepagesz=1G hugepagesz=1G hugepages=8"
```

For reference, the arguments the profile applies:

- **`isolcpus` / `nohz_full` / `rcu_nocbs` (the isolated cores)** — remove the VM's cores from the general scheduler, the timer tick, and RCU callback processing. These cores then run only the guest.
- **housekeeping IRQ affinity** — directs device interrupts to the non-isolated cores.
- **`skew_tick=1`** — desynchronizes per-CPU timer ticks so they do not fire simultaneously and cause a latency spike.
- **`intel_pstate=disable`** — assigns frequency control to a fixed governor instead of the firmware's P-state driver.

Keep `isolated_cores` (step 06), the VM's `<vcpupin>`/`<emulatorpin>` (step 10), and the `cyclictest` range (step 12) in agreement. A mismatch causes a VM that boots but does not meet latency targets.

## Relax the RT scheduling throttle

By default the kernel throttles `SCHED_FIFO`/`SCHED_RR` tasks to 95% of each second (`sched_rt_runtime_us=950000`) as a runaway safeguard. A pinned, isolated RT vCPU requires the entire core. Set the runtime to unlimited:

```bash
sudo tee /etc/sysctl.d/99-vpac-rt.conf >/dev/null <<'EOF'
# Let SCHED_FIFO RT threads (the guest vCPUs) use 100% of their isolated cores.
kernel.sched_rt_runtime_us = -1
EOF

sudo sysctl --system
```

## Pin the CPU frequency governor

In case CPU throtteling via the OS is disabled on BIOS-level, you may skip this part. Check via:
```bash
cpupower frequency-info --governors
```

If the command does not show any available options, throtteling is disabled and you may continue with the next step.

The `realtime-virtual-host` profile sets the governor to `performance` on each boot. To set and persist it explicitly as well, configure the `cpupower` service. Enabling the service alone applies whatever is in `/etc/sysconfig/cpupower`, whose default is not `performance`, so set it there first:

```bash
sudo dnf -y install kernel-tools     # provides cpupower, if not already present

# Apply immediately:
sudo cpupower frequency-set -g performance

# Persist across reboots via the cpupower service:
echo 'GOVERNOR=performance' | sudo tee /etc/sysconfig/cpupower
sudo systemctl enable --now cpupower
```

## Mount the cache controller (resctrl)

The SSC600SW host setup partitions the L3 cache with Intel CAT (`pqos`) so the relay's cores receive a protected slice. `pqos` has no effect if `/sys/fs/resctrl` is not mounted. Mount it persistently with a systemd mount unit:

```bash
sudo tee /etc/systemd/system/sys-fs-resctrl.mount >/dev/null <<'EOF'
[Unit]
Description=Mount resctrl for Intel CAT (pqos)
DefaultDependencies=no
Before=local-fs.target

[Mount]
What=resctrl
Where=/sys/fs/resctrl
Type=resctrl

[Install]
WantedBy=local-fs.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sys-fs-resctrl.mount
mount | grep resctrl     # confirm it's mounted
```

(On non-Intel hardware or CPUs without CAT, this mount may be unavailable. In that case, skip cache partitioning; the relay runs but shares L3.)

> Mounting `resctrl` enables cache partitioning; it does not *create* a partition. Until a control group exists under `/sys/fs/resctrl/` with the relay's cores and an L3 mask, the relay shares the last-level cache with the rest of the host and a busy non-RT core can evict the relay's working set. The SSC600SW host setup's `pqos` service creates this group at relay start; confirm it is enabled and that `ls /sys/fs/resctrl/` shows a group after the VM is running. To partition by hand instead, create a group, assign the relay's cores, and give it a dedicated cache-bit mask (the exact mask is hardware-specific — see `man resctrl`).

## Keep device IRQs and helper threads off the isolated cores

Isolating the cores is not enough on its own. Two runtime sources can still land work on them.

### Disable services that periodically wake every CPU

`irqbalance` re-spreads device IRQs across all CPUs and silently fights the isolation; `ksm`/`ksmtuned` scan memory. None are useful here. Disable each one that is present (on a minimal install `ksm`/`ksmtuned` may not exist — disable them individually so a missing unit does not abort the others):

```bash
for svc in irqbalance ksm ksmtuned; do
  sudo systemctl disable --now "$svc" 2>/dev/null || true
done
systemctl is-active irqbalance    # expect: inactive
```

### Pin the process-bus NIC IRQs to the housekeeping cores

`isolate_managed_irq=Y` (step 06) keeps kernel-managed IRQs off the isolated cores **at device probe time only**. Any later change to a NIC's queues or rings — an `ethtool -L`/`-G`, an interface down/up, or tuned's own `netdev_queue_count` — tears the queues down and recreates them, re-spreading the managed IRQs across *all* CPUs. That can drop a process-bus NIC's busy RX queue, and its softirq load, onto an isolated core carrying the relay. High-rate GOOSE/Sampled-Value multicast makes this a real latency source.

Pin the process-bus interfaces' IRQs back onto the housekeeping cores (the non-isolated set — e.g. `0-9` when isolated is `10-15`). Substitute the real interface names:

```bash
HK=0-9                       # housekeeping cores = all cores minus the isolated set
for nic in ens2f0 ens2f1; do # the process-bus (and any other RT-path) NICs
  for irq in /sys/class/net/$nic/device/msi_irqs/*; do
    echo "$HK" | sudo tee /proc/irq/$(basename "$irq")/smp_affinity_list >/dev/null
  done
done

# verify nothing for these NICs is on an isolated core:
grep -E 'ens2f0|ens2f1' /proc/interrupts | awk '{print $1}'   # note the IRQ numbers
cat /proc/irq/<irq>/effective_affinity_list                   # must be within 0-9
```

Make it persistent so it survives reboots and re-applies after the tuned profile settles (a small `systemd` oneshot that runs the loop above, ordered `After=network-online.target tuned.service`).

### A third source — the relay's vhost-net threads — is handled after the VM starts

The relay's `vhost-net` kernel threads (which move network traffic between host and guest) only exist once the VM is running, and left alone they run at `SCHED_OTHER` on whatever core is free, stealing jitter even though the vCPUs are pinned. Pinning them to the emulator cores is part of bringing the VM up — see [11 — Start and license](11-start-and-license.md).

## Mask desktop powerslider service

When RHEL has a GUI installed (e.g. via the default installation), there is an additional desktop power-slider service which interferes with tuneD. Disable it as follows in case a GUI package is installed:
```bash
sudo systemctl disable --now tuned-ppd.service
sudo systemctl mask tuned-ppd.service        # belt-and-suspenders: nothing can re-enable it
sudo tuned-adm profile realtime-virtual-host # re-pin (sets profile_mode=manual)
sudo systemctl is-enabled tuned.service      # confirm tuned itself stays enabled
```

## Reboot into the tuned kernel

```bash
sudo reboot
```

After it returns, verify the configuration:

```bash
uname -r                              # must show ...rt... — the RT kernel
cat /proc/cmdline                     # isolcpus / nohz_full / hugepages present
cat /sys/devices/system/cpu/isolated  # the isolated set, e.g. 10-15
grep Huge /proc/meminfo               # HugePages_Total: 8, HugePages_Free: 8 (1 GiB each)
sysctl kernel.sched_rt_runtime_us     # -1
cpupower frequency-info | grep -i governor   # performance
mount | grep resctrl                  # mounted
tuned-adm active                      # realtime-virtual-host
systemctl is-active irqbalance        # inactive (disabled above)
# no process-bus NIC IRQ on an isolated core — effective affinity must be in 0-9:
for irq in $(grep -E 'ens2f0|ens2f1' /proc/interrupts | awk -F: '{print $1}'); do
  echo "irq$irq -> $(cat /proc/irq/$irq/effective_affinity_list)"
done
```

If the RT kernel did not boot, the hugepage reservation is missing, or the isolated set is empty, correct it before continuing. The VM depends on all three.

Continue to [09 — Prepare the SSC600 bundle](09-prepare-ssc600-bundle.md).
