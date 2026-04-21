# vm_templates

**Status: stub — not yet implemented.**

Stage 80 (first half) of the vPAC site deployment.

## Planned behavior

- For each entry in `vm_catalog`, render a libvirt domain XML under `/etc/libvirt/qemu/vpac-<vm_name>.xml` using the profile under `vars/profiles/<profile>.yml` (ssc600, vpr, windows_passthrough, etc.)
- Per-VM attributes:
  - `<vcpu placement='static'>` with `<cputune>/<vcpupin>` from `pinned_cpus`, `<emulatorpin>` from `emulator_cpus`
  - `<memoryBacking><hugepages>` when `hugepages: true`
  - `<memballoon model='none'>` (no memory-balloon surprises in RT workloads)
  - No `<watchdog>` (removed per LEARNED-FIXES)
  - `<features><pmu state='off'/>` on RT VMs
  - `<cpu mode='host-passthrough'>`
  - NICs rendered per `nics[]` entries (bridge, virtio, preserved MAC if `preserve_mac: true`)
  - Disks rendered per `disks[]` (CephFS-backed qcow2 or RBD)
  - PCI passthrough devices from `pci_passthrough[]` when present
- XML files written atomically; handler restarts `libvirtd` only when config changes

## Dependencies

- `virtualization` (stage 30), `ceph_expand` (stage 60) — VM storage paths resolve here

## Tags

- `vm` — full role (currently a no-op stub)
