# Runtime configuration — after booting the image-mode host

The bootc image carries the static OS. These steps apply the per-node identity
and the workload, and are run once after the node first boots from the
installable media.

## 1. Per-host kernel arguments (isolated cores, hugepage count)

The image bakes an **example** isolated-core range and hugepage count
(`kargs.d/10-vpac-rt.toml`). Inspect the node's topology and override to match:

```bash
lscpu -e                      # per-CPU listing: core, socket, online state
# Reserve the low cores for the host; isolate the relay's vCPUs + emulator.
sudo bootc kargs --delete=isolcpus=10-15 --append=isolcpus=N-M
#   ...repeat for nohz_full, rcu_nocbs, and hugepages= as needed, then reboot.
```

Keep this range in agreement with `/etc/tuned/realtime-virtual-host-variables.conf`,
the relay VM's `vcpupin`/`emulatorpin`, and the `cyclictest` validation range.

## 2. Network identity

Apply the per-site network layout: the station-bus bridge, the reserved
process-bus NIC, and the dedicated PTP NIC. This is done with the single-node
networking play in [`networking/`](networking/) — a generic-image + per-site
var file applied with `nmstate` (the tool is baked; the config is not). The PTP
NIC must be dedicated, not shared with a macvtap VM bridge.

```bash
cd networking
cp site-vars.example.yml site-vars.yml   # fill in real NICs + addresses
ansible-playbook -i '<node-ip>,' apply-networking.yml -e @site-vars.yml -u ansible --become
```

## 3. PTP / time sync

Lay down the device-specific `ptp4l` / `timemaster` configuration (the package
and units are already in the image). When PTP is authoritative, remove NTP
sources from chrony. Same behaviour as the package-mode `ptp_*` roles.

## 4. The relay VM

The vendor VM (disk image + libvirt domain) is not part of the OS image.
Deploy it with the existing roles:

- `vm_templates` — emit the libvirt XML with CPU pinning, 1 GiB hugepage
  backing, locked memory, disabled memballoon, no watchdog, and the correct
  disk-bus / NIC-model for the vendor VM
- `vm_deploy` — place the disk and define/start the domain

The `<vcpupin>`/`<emulatorpin>` must match the isolated cores from step 1.

## 5. Validate

Run the same checks as manual-guide chapter 12 / the `validate` role:

```bash
uname -r                                # ...rt... kernel
cat /sys/devices/system/cpu/isolated    # the isolated set
grep Huge /proc/meminfo                 # HugePages_Total matches the reservation
sysctl kernel.sched_rt_runtime_us       # -1
cpupower frequency-info | grep -i governor   # performance
mount | grep resctrl                    # mounted
tuned-adm active                        # realtime-virtual-host
virt-host-validate qemu
# cyclictest across the isolated cores for the latency baseline
```
