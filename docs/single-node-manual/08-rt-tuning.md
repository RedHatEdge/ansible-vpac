# 08 — Real-time tuning

This step configures the host as a real-time host. It boots the `kernel-rt` kernel, confirms the kernel command line (CPU isolation applied by tuned, hugepages from step 06), adjusts the RT throttle, fixes the CPU governor, and mounts the cache controller. After the final reboot, the host is ready for the relay.

## Boot the real-time kernel

`kernel-rt` was installed in step 04. Make it the default and confirm tuned's RT profile is active:

```bash
# vpac-rt-host (set in step 06) selects kernel-rt automatically on
# reboot if it's installed; make it explicit and current:
sudo grubby --info=ALL | grep -E 'kernel|rt' | head

# Ensure the rt kernel is the default. Select the newest rt kernel explicitly;
# a bare glob can match several entries after updates.
sudo grubby --set-default "$(ls -1 /boot/vmlinuz-*rt* | sort -V | tail -1)"
```

## Confirm the kernel command line

The `vpac-rt-host` profile generates the isolation and scheduling kernel arguments, through the `realtime-virtual-host` profile it includes, from the `isolated_cores` value set in step 06 — `isolcpus`, `nohz_full`, `rcu_nocbs`, `skew_tick`, `intel_pstate=disable`, and the housekeeping IRQ affinity. tuned applies them to the kernel command line on the reboot at the end of this step.

Do **not** add these arguments again with `grubby`. A second copy produces a duplicate `isolcpus=` entry that the kernel may resolve unpredictably. To change which cores are isolated, edit `isolated_cores` in `/etc/tuned/realtime-virtual-host-variables.conf` (step 06) and re-apply the profile (`sudo tuned-adm profile vpac-rt-host`) — not the kernel command line.

The same profile carries the **1 GiB hugepage reservation** (its `cmdline_vpac` line, step 06), so tuned manages it too. Confirm the arguments tuned will apply:

```bash
sudo grub2-editenv list | grep tuned_params       # isolcpus, nohz_full, rcu_nocbs, hugepages=8 and rdt=l3cat
```

A host that already received the hugepage arguments through `grubby` carries a second copy of them. Remove that copy, so tuned's is the only one:

```bash
sudo grubby --update-kernel=ALL --remove-args="default_hugepagesz hugepagesz hugepages"
```

For reference, the arguments the profile applies:

- **`isolcpus` / `nohz_full` / `rcu_nocbs` (the isolated cores)** — remove the VM's cores from the general scheduler, the timer tick, and RCU callback processing. These cores then run only the guest.
- **housekeeping IRQ affinity** — directs device interrupts to the non-isolated cores.
- **`skew_tick=1`** — desynchronizes per-CPU timer ticks so they do not fire simultaneously and cause a latency spike.
- **`intel_pstate=disable`** — assigns frequency control to a fixed governor instead of the firmware's P-state driver.
- **`default_hugepagesz=1G hugepagesz=1G hugepages=8`** — the 1 GiB hugepage reservation for the guest, from the profile's own `cmdline_vpac` line.
- **`rdt=l3cat`** — turns on L3 cache allocation, which the kernel leaves off by default on Skylake-SP and Cascade Lake Xeons (family 6, model 85), so ABB's setup service (step 09) can give the relay its own L3 partition. Also from `cmdline_vpac`.

Keep `isolated_cores` (step 06), the VM's `<vcpupin>`/`<emulatorpin>` (step 10), and the `cyclictest` range (step 12) in agreement. A mismatch causes a VM that boots but does not meet latency targets.

## Relax the RT scheduling throttle

By default the kernel throttles `SCHED_FIFO`/`SCHED_RR` tasks to 95% of each second (`sched_rt_runtime_us=950000`) as a runaway safeguard. A pinned, isolated RT vCPU requires the entire core, so the runtime is set to unlimited (`kernel.sched_rt_runtime_us = -1`). The NMI watchdog is turned off as well (`kernel.nmi_watchdog = 0`); it fires periodic non-maskable interrupts on every core, including the isolated ones.

Both values are in the `[sysctl]` section of the `vpac-rt-host` profile (step 06) and agree with what `realtime-virtual-host` already sets. Re-apply the profile and confirm:

```bash
sudo tuned-adm profile vpac-rt-host
sysctl kernel.sched_rt_runtime_us     # -1
sysctl kernel.nmi_watchdog            # 0
```

These values follow the profile: `tuned-adm off` or a switch to another profile reverts them, as it does the isolation arguments. Keep them in the profile only; a second copy in `/etc/sysctl.d/` is applied as well and can drift from it.

## Pin the CPU frequency governor

If the BIOS controls CPU frequency, the OS has no governor to set and this section does not apply. Check:

```bash
sudo dnf -y install kernel-tools     # provides cpupower, if not already present
cpupower frequency-info --governors
```

If the command lists no governors, the BIOS owns frequency control; continue with the next section.

The `vpac-rt-host` profile sets the governor to `performance` at every boot, on RHEL 9 and RHEL 10 (inherited from `latency-performance` through `realtime-virtual-host`). Confirm it:

```bash
cpupower frequency-info | grep -i governor   # The governor "performance"
```

> **RHEL 9 vs RHEL 10.** RHEL 9's `kernel-tools` also ships a `cpupower.service`; its default `/etc/sysconfig/cpupower` already selects `performance` (`CPUPOWER_START_OPTS="frequency-set -g performance"`). RHEL 10's `kernel-tools` has no such service, and no RHEL 10 package provides one. The tuned profile is the persistence on both releases, so this guide does not use the service. If you enable it on RHEL 9 anyway, leave `/etc/sysconfig/cpupower` at its default: the service reads only `CPUPOWER_START_OPTS`.

## Mount the cache controller (resctrl)

ABB's host setup service (step 09) partitions the L3 cache with Intel CAT (`pqos`) so the relay's cores receive a protected slice. It needs `/sys/fs/resctrl` mounted and L3 cache allocation turned on in the kernel (`rdt=l3cat`, step 06). Mount it persistently with a systemd mount unit:

```bash
sudo vi /etc/systemd/system/sys-fs-resctrl.mount
```

```ini
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
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sys-fs-resctrl.mount
mount | grep resctrl     # confirm it's mounted
```

The verification block after the reboot confirms L3 cache allocation is on (`/sys/fs/resctrl/info/L3` exists). A CPU without it (no `cat_l3` in `/proc/cpuinfo`) cannot run ABB's cache partition.

> Mounting `resctrl` enables cache partitioning; it does not *create* a partition. Until a class exists with the relay's cores and an L3 mask, the relay shares the last-level cache with the rest of the host and a busy non-RT core can evict the relay's working set. ABB's setup service (step 09) creates it at every boot.

## Keep device IRQs and helper threads off the isolated cores

Isolating the cores is not enough on its own. Two runtime sources can still land work on them.

### Disable services that periodically wake every CPU

`irqbalance` re-spreads device IRQs across all CPUs and silently fights the isolation; `ksm`/`ksmtuned` scan memory to merge identical pages. None are useful here.

`ksm`/`ksmtuned` exist only if the optional `ksmtuned` package is installed; it is not installed by default on RHEL 9 or RHEL 10. The tuned profile stops KSM either way (`/sys/kernel/mm/ksm/run` reads `2`; once ABB's setup service from step 09 runs, it reads `0` — both mean stopped). If the units are present, disable them:

```bash
systemctl list-unit-files ksm.service ksmtuned.service   # "0 unit files listed": skip the next two commands
sudo systemctl disable --now ksm
sudo systemctl disable --now ksmtuned
cat /sys/kernel/mm/ksm/run            # 2 (0 after step 09)

# irqbalance is MASKED, not just disabled. RHEL ships it enabled, so a package
# reinstall or `systemctl preset` puts a merely-disabled unit back. Masking
# survives both. (To convert this host to a non-RT role later:
# `sudo systemctl unmask irqbalance`.)
sudo systemctl disable --now irqbalance
sudo systemctl mask irqbalance

systemctl is-active  irqbalance    # expect: inactive
systemctl is-enabled irqbalance    # expect: masked
```

### Process-bus NIC IRQs: placed by ABB's setup service

The housekeeping IRQ affinity and `isolate_managed_irq=Y` (step 06) keep device interrupts off the isolated cores at boot. ABB's setup service (step 09) then moves each process-bus NIC's interrupts to the process-bus IRQ core(s) from step 06, at every boot. There is nothing to run for them in this step.

`isolate_managed_irq=Y` holds **at device probe time only**. Any later change to a NIC's queues or rings — an `ethtool -L`/`-G`, an interface down/up, or tuned's own `netdev_queue_count` — tears the queues down and recreates them, and can spread their interrupts across *all* CPUs, isolated ones included. After such a change, re-apply the placement with `sudo systemctl restart ssc600-startup.service`.

### Keep the RDMA driver off the NICs

Some Intel ports (`ice` on E810, `i40e` on X722) register an RDMA auxiliary device at probe, and RHEL
autoloads `irdma` onto it. Check first:

```bash
lsmod | grep -w irdma       # no output: the driver is not bound — skip the rest of this section
```

While `irdma` is bound, the port **refuses every channel change** — tuned's
`netdev_queue_count`, and any `ethtool -L` — logging only `Cannot change channels when RDMA is
active` in `dmesg`. Nothing else reports the refusal, so the queue layout is whatever the driver
chose at probe rather than what was configured. Nothing on a protection host uses RDMA. Blacklist
the driver and stop it loading as a dependency:

```bash
sudo vi /etc/modprobe.d/vpac-no-rdma.conf
```

```
# RDMA is unused on a protection host. While irdma is bound to an ice/i40e
# port the port refuses channel changes (ethtool -L, tuned netdev_queue_count).
blacklist irdma
install irdma /bin/false
```

The initramfs rebuild before the reboot (below) carries the blacklist into early boot. The change takes effect at the reboot at the end of this step; the verification block below checks it.

### A third source — the relay's vhost-net threads — is handled after the VM starts

The relay's `vhost-net` kernel threads (which move network traffic between host and guest) only exist once the VM is running, and left alone they run at `SCHED_OTHER` on whatever core is free, stealing jitter even though the vCPUs are pinned. Pinning them to the emulator cores is part of bringing the VM up — see [11 — Start and license](11-start-and-license.md).

## Set the KVM and vhost-net module options

These are the module options the `virtualization` role applies on Intel hosts:

- `ple_gap=0 ple_window=0` turn off Pause-Loop Exiting. PLE makes a vCPU that spin-waits exit to the host so another vCPU can run. Each RT vCPU has a dedicated core, so the exit only adds latency. Both values must be 0.
- `enable_apicv=n` turns off APIC virtualization.
- `nested=1` allows nested virtualization.
- `experimental_zcopytx=1` enables zero-copy transmit in `vhost-net`.

Create the drop-in:

```bash
sudo vi /etc/modprobe.d/vpac-kvm.conf
```

```
# KVM and vhost-net options for the relay VM (Intel hosts).
options kvm_intel nested=1 enable_apicv=n ple_gap=0 ple_window=0
options vhost_net experimental_zcopytx=1
```

The `kvm_intel` options take effect at the reboot at the end of this step; the verification block below checks them. `vhost_net` loads when the VM starts, so step 12 checks its option. The `realtime-virtual-host` profile also writes `/etc/modprobe.d/kvm.rt.tuned.conf`, which sets `ple_gap=0` as well; the two files agree.

## Mask desktop powerslider service

When RHEL has a GUI installed (e.g. via the default installation), there is an additional desktop power-slider service which interferes with tuneD. Disable it as follows in case a GUI package is installed:
```bash
sudo systemctl disable --now tuned-ppd.service
sudo systemctl mask tuned-ppd.service        # belt-and-suspenders: nothing can re-enable it
sudo tuned-adm profile vpac-rt-host          # re-pin (sets profile_mode=manual)
sudo systemctl is-enabled tuned.service      # confirm tuned itself stays enabled
```

## Rebuild the initramfs

Rebuild the initramfs for every installed kernel, so it carries the modprobe drop-ins from this step (`vpac-kvm.conf`, and `vpac-no-rdma.conf` if you created it):

```bash
sudo dracut -f --regenerate-all
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
sysctl kernel.nmi_watchdog            # 0
cpupower frequency-info | grep -i governor   # performance
mount | grep resctrl                  # mounted
ls /sys/fs/resctrl/info/              # includes L3 — L3 cache allocation is on (rdt=l3cat, step 06);
                                      # mounted without L3 means allocation is still off
cat /sys/fs/resctrl/info/L3/cbm_mask  # the L3 ways, for the step 09 cache masks
tuned-adm active                      # vpac-rt-host
getenforce                            # Disabled (step 04)
sysctl net.ipv4.ip_forward            # 0 (libvirt default network removed in step 05)
systemctl is-active irqbalance        # inactive (masked above)
lsmod | grep -w irdma                 # no output — RDMA driver not bound
ls /sys/class/infiniband/ 2>/dev/null # no output
grep -H . /sys/module/kvm_intel/parameters/{nested,enable_apicv,ple_gap,ple_window}   # Y, N, 0, 0
# no process-bus NIC IRQ on an isolated core — effective affinity must be in 0-9:
for irq in $(grep -E 'ens2f0|ens2f1' /proc/interrupts | awk -F: '{print $1}'); do
  echo "irq$irq -> $(cat /proc/irq/$irq/effective_affinity_list)"
done
```

If the RT kernel did not boot, the hugepage reservation is missing, or the isolated set is empty, correct it before continuing. The VM depends on all three.

Continue to [09 — Prepare the SSC600 bundle](09-prepare-ssc600-bundle.md).
