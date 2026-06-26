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

Decide the core split. Inspect the topology:

```bash
lscpu
lscpu -e        # per-CPU listing: core, socket, online state
```

Select a contiguous block of physical cores at the **high end** for the VM. For example, on a host giving the SSC600SW four vCPUs plus two emulator threads, isolate six cores and reserve the top of them. The exact indices depend on the CPU — a 16-core part and a 24-core part do not use the same indices. In all cases, isolate the VM's pinned cores and emulator cores, and reserve at least the first one or two cores for the host.

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
